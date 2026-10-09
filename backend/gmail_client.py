"""Gmail API calls, and parsing of the message data they return."""

import base64
from datetime import datetime, timezone

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource, build

# On a rate limit (429 or 403) or a server error (5xx), the client library waits and retries,
# doubling the wait each time (exponential backoff). Without this, the first one crashes the sync.
NUM_RETRIES = 5


def build_service(creds: Credentials) -> Resource:
    """Return a Gmail API client authorized with these credentials."""
    return build("gmail", "v1", credentials=creds)


def list_message_ids(service: Resource, max_results: int) -> list[str]:
    """Return the IDs of the newest messages in the mailbox."""
    result = (
        service.users()
        .messages()
        .list(userId="me", maxResults=max_results)
        .execute(num_retries=NUM_RETRIES)
    )
    return [message["id"] for message in result.get("messages", [])]


def list_all_message_ids(service: Resource, query: str) -> list[str]:
    """Return the IDs of every message matching a Gmail search query, following all pages."""
    message_ids = []
    page_token = None  # no token means "first page"
    while True:
        result = (
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=500, pageToken=page_token)
            .execute(num_retries=NUM_RETRIES)
        )
        message_ids.extend(message["id"] for message in result.get("messages", []))
        page_token = result.get("nextPageToken")
        if page_token is None:  # no more pages
            return message_ids


def get_profile(service: Resource) -> dict:
    """Return the mailbox's profile, including "emailAddress" and its current "historyId"."""
    return service.users().getProfile(userId="me").execute(num_retries=NUM_RETRIES)


def get_message(service: Resource, message_id: str) -> dict:
    """Fetch one message and return its sender, subject, arrival time (UTC), body and labels."""
    message = (
        service.users()
        .messages()
        .get(userId="me", id=message_id, format="full")
        .execute(num_retries=NUM_RETRIES)
    )
    payload = message["payload"]
    label_ids = message.get("labelIds", [])
    return {
        "id": message["id"],
        "sender": _get_header(payload["headers"], "From"),
        "subject": _get_header(payload["headers"], "Subject"),
        "received": datetime.fromtimestamp(int(message["internalDate"]) / 1000, tz=timezone.utc),
        "body": _find_body(payload, "text/plain") or _find_body(payload, "text/html"),
        "label_ids": label_ids,  # e.g. ["INBOX", "UNREAD", "CATEGORY_PROMOTIONS"]
        "is_read": "UNREAD" not in label_ids,
    }


def _find_body(part: dict, mime_type: str) -> str | None:
    """Return the decoded text of the first part with this MIME type, or None."""
    if part["mimeType"] == mime_type and "data" in part["body"]:
        data = part["body"]["data"]
        data += "=" * (-len(data) % 4)
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

    for child in part.get("parts", []):
        text = _find_body(child, mime_type)
        if text is not None:
            return text

    return None


def _get_header(headers: list[dict], name: str) -> str | None:
    """Return the value of the first header with this name, or None."""
    for header in headers:
        if header["name"].lower() == name.lower():
            return header["value"]
    return None
