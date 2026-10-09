"""Sync one Gmail account's inbox into the SQLite database."""

import sqlite3
from pathlib import Path

from googleapiclient.discovery import Resource
from googleapiclient.errors import HttpError

import db
from gmail_auth import get_credentials
from gmail_client import (
    build_service,
    get_message,
    get_profile,
    list_all_message_ids,
    list_history,
)

TOKEN_PATH = Path(__file__).parent / "token.json"  # moves to tokens/<email>.json in Step 5
FULL_SYNC_QUERY = "in:inbox newer_than:30d"


def save_messages(
    conn: sqlite3.Connection, service: Resource, account: str, message_ids: list[str]
) -> int:
    """Fetch and save each message. Return how many were new. The caller commits."""
    print(f"Found {len(message_ids)} messages")
    new_count = 0
    for i, message_id in enumerate(message_ids, start=1):
        try:
            message = get_message(service, message_id)
        except HttpError as error:
            if error.resp.status == 404:  # deleted since we listed it
                continue
            raise
        if db.save_item(conn, account, message):
            new_count += 1
        if i % 50 == 0:
            print(f"  {i}/{len(message_ids)}")
    return new_count


def full_sync(conn: sqlite3.Connection, service: Resource, account: str) -> int:
    """Save every inbox email from the last 30 days and bookmark the mailbox's historyId.
    Return the number of new emails saved."""
    # Take the bookmark before listing, so an email arriving mid-sync can't fall in a gap.
    bookmark = get_profile(service)["historyId"]
    message_ids = list_all_message_ids(service, FULL_SYNC_QUERY)
    new_count = save_messages(conn, service, account, message_ids)

    # Save the emails and the bookmark in one commit: either both are stored or neither is.
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
    creds = get_credentials(TOKEN_PATH)
    service = build_service(creds)
    conn = db.connect()
    try:
        account = get_profile(service)["emailAddress"]
        db.add_account(conn, account, TOKEN_PATH)
        new_count = sync_account(conn, service, account)
        print(f"{account}: {new_count} new emails")
    except HttpError as error:
        print(f"Gmail API error: {error}")
    finally:
        conn.close()  # anything not committed is thrown away


if __name__ == "__main__":
    main()
