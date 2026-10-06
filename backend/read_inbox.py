"""Print the newest emails from one Gmail account."""

from pathlib import Path

from googleapiclient.errors import HttpError

from gmail_auth import get_credentials
from gmail_client import build_service, get_message, list_message_ids

TOKEN_PATH = Path(__file__).parent / "token.json"
NUM_EMAILS = 10


def main() -> None:
    creds = get_credentials(TOKEN_PATH)
    service = build_service(creds)

    try:
        message_ids = list_message_ids(service, NUM_EMAILS)
        if not message_ids:
            print("No messages found.")
            return

        for message_id in message_ids:
            message = get_message(service, message_id)
            print(f"From: {message['sender']}")
            print(f"Subject: {message['subject']}")
            print(f"Received: {message['received'].astimezone():%Y-%m-%d %H:%M}")
            print(message["body"])
            print("-" * 40)
    except HttpError as error:
        print(f"Gmail API error: {error}")


if __name__ == "__main__":
    main()
