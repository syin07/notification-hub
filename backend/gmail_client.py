"""Gmail API calls, and parsing of the message data they return."""

import base64
import re
from datetime import datetime, timezone
from html.parser import HTMLParser

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import Resource, build

# On a rate limit (429 or 403) or a server error (5xx), the client library waits and retries,
# doubling the wait each time (exponential backoff). Without this, the first one crashes the sync.
NUM_RETRIES = 5


def build_service(creds: Credentials) -> Resource:
    """Return a Gmail API client authorized with these credentials."""
    return build("gmail", "v1", credentials=creds)


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


def list_history(service: Resource, start_history_id: str) -> tuple[list[str], str]:
    """Return the IDs of messages added to the inbox since start_history_id, and the mailbox's
    current historyId (the new bookmark). Gmail keeps about a week of history; for an older
    start_history_id this raises HttpError with status 404."""
    message_ids = []
    page_token = None
    while True:
        result = (
            service.users()
            .history()
            .list(
                userId="me",
                startHistoryId=start_history_id,
                historyTypes=["messageAdded"],
                labelId="INBOX",
                maxResults=500,
                pageToken=page_token,
            )
            .execute(num_retries=NUM_RETRIES)
        )
        for record in result.get("history", []):
            for added in record.get("messagesAdded", []):
                message_ids.append(added["message"]["id"])
        page_token = result.get("nextPageToken")
        if page_token is None:
            # dict.fromkeys drops duplicate IDs and keeps the order.
            return list(dict.fromkeys(message_ids)), result["historyId"]


def get_profile(service: Resource) -> dict:
    """Return the mailbox's profile, including "emailAddress" and its current "historyId"."""
    return service.users().getProfile(userId="me").execute(num_retries=NUM_RETRIES)


def get_message(service: Resource, message_id: str) -> dict:
    """Fetch one message and return its thread, sender, recipients, subject, arrival time (UTC),
    plain-text body and labels."""
    message = (
        service.users()
        .messages()
        .get(userId="me", id=message_id, format="full")
        .execute(num_retries=NUM_RETRIES)
    )
    payload = message["payload"]
    label_ids = message.get("labelIds", [])

    body = _find_body(payload, "text/plain")
    if body and _looks_like_html(body):  # some senders put HTML in the plain-text part
        body = html_to_text(body)
    if not body:  # no plain-text part, or an empty one: use the HTML part
        html = _find_body(payload, "text/html")
        body = html_to_text(html) if html else body

    return {
        "id": message["id"],
        "thread_id": message["threadId"],
        "sender": _get_header(payload["headers"], "From"),
        "to": _get_header(payload["headers"], "To"),
        "cc": _get_header(payload["headers"], "Cc"),
        "subject": _get_header(payload["headers"], "Subject"),
        "received": datetime.fromtimestamp(int(message["internalDate"]) / 1000, tz=timezone.utc),
        "body": body,
        "label_ids": label_ids,  # e.g. ["INBOX", "UNREAD", "CATEGORY_PROMOTIONS"]
        "is_read": "UNREAD" not in label_ids,
    }


def html_to_text(html: str) -> str:
    """Return the visible text of an HTML email: no tags, one line per block, no blank lines."""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    text = "".join(parser.parts)
    # split() with no argument splits on any run of whitespace (including &nbsp;), so this
    # squeezes each line's spaces down to single ones.
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line)


def _looks_like_html(text: str) -> bool:
    """Return True if text contains closing tags of HTML page structure.
    Plain text often has <https://...> links, which must not be parsed as tags."""
    return re.search(r"</(html|body|div|table|tr|td|p)\s*>", text, re.IGNORECASE) is not None


class _TextExtractor(HTMLParser):
    """Collects the text of an HTML document as feed() walks through it.

    HTMLParser calls handle_starttag at each <tag>, handle_endtag at each </tag> and
    handle_data for the text between tags. It also turns entities like &amp; into &.
    """

    # Their contents are code or metadata, not text a reader sees.
    SKIP_TAGS = {"script", "style", "title"}
    # Tags that start a new line when a browser shows the page.
    LINE_BREAK_TAGS = {
        "br", "p", "div", "tr", "li", "ul", "ol", "table", "hr", "blockquote",
        "h1", "h2", "h3", "h4", "h5", "h6",
    }

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip_depth = 0  # above 0 while inside a SKIP_TAGS element

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self.SKIP_TAGS:
            self.skip_depth += 1
        elif tag in self.LINE_BREAK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP_TAGS:
            self.skip_depth = max(0, self.skip_depth - 1)
        elif tag in self.LINE_BREAK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip_depth == 0:
            self.parts.append(data)


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
