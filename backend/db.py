"""SQLite storage: open the database and create the tables."""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "notification_hub.db"

# While on SQLite, change the schema by editing this and deleting the .db file. Once the
# database holds AI results (Block 3), deleting it throws away paid LLM calls: migrate instead.
SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    email       TEXT PRIMARY KEY,
    token_path  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
    id           INTEGER PRIMARY KEY,
    source       TEXT NOT NULL,                     -- 'gmail' for now
    account      TEXT NOT NULL REFERENCES accounts(email),
    external_id  TEXT NOT NULL,                     -- the source's own message ID
    thread_id    TEXT,                              -- the source's conversation ID
    sender       TEXT,
    to_addrs     TEXT,                              -- the To header, as sent
    cc_addrs     TEXT,                              -- the Cc header, as sent
    subject      TEXT,
    body         TEXT,                              -- plain text; HTML-only emails are stripped
    received_at  TEXT NOT NULL,                     -- ISO 8601 in UTC
    is_read      INTEGER NOT NULL,                  -- 0 or 1
    labels       TEXT,                              -- comma-separated, as of the first save
    UNIQUE (account, external_id)
);

CREATE TABLE IF NOT EXISTS sync_state (
    account     TEXT PRIMARY KEY REFERENCES accounts(email),
    history_id  TEXT NOT NULL
);

-- AI results, kept apart from the source data in items. One row per item, model and prompt
-- version, so changing the prompt (a new version) re-runs deliberately and keeps old results.
CREATE TABLE IF NOT EXISTS item_analysis (
    id                 INTEGER PRIMARY KEY,
    item_id            INTEGER NOT NULL REFERENCES items(id),
    model              TEXT NOT NULL,
    prompt_version     TEXT NOT NULL,
    status             TEXT NOT NULL CHECK (status IN ('ok', 'failed')),
    attempts           INTEGER NOT NULL,                 -- API calls made for this row
    category           TEXT CHECK (category IN ('action_required', 'fyi', 'bulk')),
    importance         INTEGER CHECK (importance BETWEEN 1 AND 5),
    reason             TEXT,                             -- one line, grounded in the email
    deadline           TEXT,                             -- YYYY-MM-DD, a date in Los Angeles
    deadline_evidence  TEXT,                             -- the email's own words for the deadline
    error              TEXT,                             -- why it failed; never email text
    input_tokens       INTEGER NOT NULL,                 -- summed over all attempts
    output_tokens      INTEGER NOT NULL,
    analyzed_at        TEXT NOT NULL,                    -- ISO 8601 in UTC, of the last attempt
    UNIQUE (item_id, model, prompt_version),
    -- Never half-saved: a successful row has every required field.
    CHECK (status = 'failed' OR (category IS NOT NULL AND importance IS NOT NULL
                                 AND reason IS NOT NULL)),
    -- No deadline without the words it came from.
    CHECK ((deadline IS NULL) = (deadline_evidence IS NULL))
);
"""


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Open the database, create any missing tables, and return the connection."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # rows can be read by column name: row["subject"]
    conn.execute("PRAGMA foreign_keys = ON")  # SQLite leaves REFERENCES unchecked unless asked
    conn.executescript(SCHEMA)
    return conn


def add_account(conn: sqlite3.Connection, email: str, token_path: Path) -> None:
    """Register an account. Does nothing if it's already registered."""
    conn.execute(
        "INSERT OR IGNORE INTO accounts (email, token_path) VALUES (?, ?)",
        (email, str(token_path)),
    )


def list_accounts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return every registered account (email, token_path), sorted by email."""
    return conn.execute("SELECT email, token_path FROM accounts ORDER BY email").fetchall()


def has_item(conn: sqlite3.Connection, account: str, external_id: str) -> bool:
    """Return True if this account's item with this external ID is already stored."""
    row = conn.execute(
        "SELECT 1 FROM items WHERE account = ? AND external_id = ?", (account, external_id)
    ).fetchone()
    return row is not None


def save_item(conn: sqlite3.Connection, account: str, message: dict) -> bool:
    """Store one Gmail message (a dict from get_message).
    Return True if it was new, False if it was already stored."""
    source = "gmail"  # for now
    external_id = message["id"]
    thread_id = message["thread_id"]
    sender = message["sender"]
    to_addrs = message["to"]
    cc_addrs = message["cc"]
    subject = message["subject"]
    body = message["body"]
    received_at = message["received"].isoformat()
    is_read = message["is_read"]
    labels = ",".join(message["label_ids"])
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO items
            (source, account, external_id, thread_id, sender, to_addrs, cc_addrs,
             subject, body, received_at, is_read, labels)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (source, account, external_id, thread_id, sender, to_addrs, cc_addrs,
         subject, body, received_at, is_read, labels),
    )
    return cursor.rowcount == 1


def get_history_id(conn: sqlite3.Connection, account: str) -> str | None:
    """Return the account's sync bookmark, or None if it has never been synced."""
    row = conn.execute(
        "SELECT history_id FROM sync_state WHERE account = ?", (account,)
    ).fetchone()
    return row["history_id"] if row else None


def save_history_id(conn: sqlite3.Connection, account: str, history_id: str) -> None:
    """Store the account's sync bookmark, replacing any older one."""
    conn.execute(
        "INSERT OR REPLACE INTO sync_state (account, history_id) VALUES (?, ?)",
        (account, history_id),
    )




def items_to_classify(
    conn: sqlite3.Connection, model: str, prompt_version: str, max_attempts: int, limit: int
) -> list[sqlite3.Row]:
    """Return up to `limit` items, newest first, that still need a result for this model and
    prompt version: never tried, or failed fewer than max_attempts times."""
    # LEFT JOIN keeps every item, with the analysis columns NULL where no row matches,
    # so "a.id IS NULL" means "never tried with this model and prompt version".
    return conn.execute(
        """
        SELECT items.id, items.account, items.sender, items.to_addrs, items.cc_addrs,
               items.subject, items.body, items.received_at, items.labels
        FROM items
        LEFT JOIN item_analysis AS a
            ON a.item_id = items.id AND a.model = ? AND a.prompt_version = ?
        WHERE a.id IS NULL OR (a.status = 'failed' AND a.attempts < ?)
        ORDER BY items.received_at DESC
        LIMIT ?
        """,
        (model, prompt_version, max_attempts, limit),
    ).fetchall()


def save_analysis(
    conn: sqlite3.Connection,
    item_id: int,
    model: str,
    prompt_version: str,
    result: dict | None,
    error: str | None,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Store one classification attempt: a validated result, or None and the error.

    A retry updates the item's failed row. A successful row is never overwritten, so an
    item is never re-scored by accident; a new prompt version gets a new row instead."""
    status = "ok" if result is not None else "failed"
    result = result or {}
    analyzed_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO item_analysis
            (item_id, model, prompt_version, status, attempts, category, importance, reason,
             deadline, deadline_evidence, error, input_tokens, output_tokens, analyzed_at)
        VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (item_id, model, prompt_version) DO UPDATE SET
            status            = excluded.status,
            attempts          = item_analysis.attempts + 1,
            category          = excluded.category,
            importance        = excluded.importance,
            reason            = excluded.reason,
            deadline          = excluded.deadline,
            deadline_evidence = excluded.deadline_evidence,
            error             = excluded.error,
            input_tokens      = item_analysis.input_tokens + excluded.input_tokens,
            output_tokens     = item_analysis.output_tokens + excluded.output_tokens,
            analyzed_at       = excluded.analyzed_at
        WHERE item_analysis.status = 'failed'
        """,
        (item_id, model, prompt_version, status,
         result.get("category"), result.get("importance"), result.get("reason"),
         result.get("deadline"), result.get("deadline_evidence"),
         error, input_tokens, output_tokens, analyzed_at),
    )
