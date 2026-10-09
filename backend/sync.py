"""Sync every registered Gmail account's inbox into the SQLite database."""

import sqlite3
from pathlib import Path

from googleapiclient.discovery import Resource
from googleapiclient.errors import HttpError

import db
from gmail_auth import ReauthorizationNeeded, load_credentials
from gmail_client import (
    build_service,
    get_message,
    get_profile,
    list_all_message_ids,
    list_history,
)

FULL_SYNC_QUERY = "in:inbox newer_than:30d"
BATCH_SIZE = 50


def save_messages(
    conn: sqlite3.Connection, service: Resource, account: str, message_ids: list[str]
) -> int:
    """Fetch and save each message that isn't stored yet. Return how many were new.

    Commits every BATCH_SIZE messages, so a sync that fails partway keeps what it saved
    and the next run skips those. The caller saves the bookmark and makes the last commit.
    """
    # Skipping stored messages is a quick local check; fetching them again is a Gmail call.
    to_fetch = [m for m in message_ids if not db.has_item(conn, account, m)]
    print(f"Found {len(message_ids)} messages, {len(to_fetch)} not stored yet")

    new_count = 0
    for i, message_id in enumerate(to_fetch, start=1):
        try:
            message = get_message(service, message_id)
        except HttpError as error:
            if error.resp.status == 404:  # deleted since we listed it
                continue
            raise
        if db.save_item(conn, account, message):
            new_count += 1
        if i % BATCH_SIZE == 0:
            conn.commit()
            print(f"  {i}/{len(to_fetch)}")
    return new_count


def full_sync(conn: sqlite3.Connection, service: Resource, account: str) -> int:
    """Save every inbox email from the last 30 days and bookmark the mailbox's historyId.
    Return the number of new emails saved."""
    # Take the bookmark before listing, so an email arriving mid-sync can't fall in a gap.
    bookmark = get_profile(service)["historyId"]
    message_ids = list_all_message_ids(service, FULL_SYNC_QUERY)
    new_count = save_messages(conn, service, account, message_ids)

    # Save the bookmark only after every email is saved. Emails without a bookmark are
    # harmless (the next run skips them); a bookmark without its emails would lose them.
    db.save_history_id(conn, account, bookmark)
    conn.commit()
    return new_count


def incremental_sync(
    conn: sqlite3.Connection, service: Resource, account: str, bookmark: str
) -> int:
    """Save the inbox emails added since the bookmark, then move the bookmark forward.
    Return the number of new emails saved."""
    message_ids, new_bookmark = list_history(service, bookmark)
    new_count = save_messages(conn, service, account, message_ids)

    db.save_history_id(conn, account, new_bookmark)
    conn.commit()
    return new_count


def sync_account(conn: sqlite3.Connection, service: Resource, account: str) -> int:
    """Sync one account: incrementally if it has a bookmark, in full if not or if it expired.
    Return the number of new emails saved."""
    bookmark = db.get_history_id(conn, account)
    if bookmark is None:
        print("No bookmark yet: running a full sync")
        return full_sync(conn, service, account)

    try:
        return incremental_sync(conn, service, account, bookmark)
    except HttpError as error:
        if error.resp.status != 404:
            raise
        print("Bookmark expired (Gmail keeps about a week of history): running a full sync")
        return full_sync(conn, service, account)


def main() -> None:
    conn = db.connect()
    try:
        accounts = db.list_accounts(conn)
        if not accounts:
            print("No accounts yet: add one with add_account.py")
            return

        # One account's problem must not stop the others, so each is synced in its own try.
        results = {}
        for account in accounts:
            email = account["email"]
            print(f"\n== {email}")
            try:
                creds = load_credentials(Path(account["token_path"]))
                new_count = sync_account(conn, build_service(creds), email)
                results[email] = f"{new_count} new emails"
            except ReauthorizationNeeded as error:
                results[email] = f"skipped, {error}: run add_account.py and pick this account"
            except HttpError as error:
                # Throw away this account's unfinished batch, so the next account starts
                # with a clean transaction. Committed batches stay; the next run skips them.
                conn.rollback()
                results[email] = f"Gmail API error: {error}"

        print("\nSummary")
        for email, result in results.items():
            print(f"  {email}: {result}")
    finally:
        conn.close()  # anything not committed is thrown away


if __name__ == "__main__":
    main()
