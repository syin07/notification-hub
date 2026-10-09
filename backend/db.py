"""SQLite storage: open the database and create the tables."""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "notification_hub.db"

# While on SQLite, change the schema by editing this and deleting the .db file.
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
    sender       TEXT,
    subject      TEXT,
    body         TEXT,
    received_at  TEXT NOT NULL,                     -- ISO 8601 in UTC
    is_read      INTEGER NOT NULL,                  -- 0 or 1
    priority     INTEGER CHECK (priority BETWEEN 1 AND 5),  -- NULL until ranked (Block 3)
    category     TEXT CHECK (category IN
                     ('recruiting', 'school', 'newsletters', 'personal', 'other')),
    UNIQUE (account, external_id)
);

CREATE TABLE IF NOT EXISTS sync_state (
    account     TEXT PRIMARY KEY REFERENCES accounts(email),
    history_id  TEXT NOT NULL
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


def save_item(conn: sqlite3.Connection, account: str, message: dict) -> bool:
    """Store one Gmail message (a dict from get_message).
    Return True if it was new, False if it was already stored."""
    source = "gmail"  # for now
    external_id = message["id"]
    sender = message["sender"]
    subject = message["subject"]
    body = message["body"]
    received_at = message["received"].isoformat()
    is_read = message["is_read"]
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO items
            (source, account, external_id, sender, subject, body, received_at, is_read)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (source, account, external_id, sender, subject, body, received_at, is_read),
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


