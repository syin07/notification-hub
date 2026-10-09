"""OAuth 2.0 for Gmail: authorize an account in the browser, or load its saved token."""

from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# If you change the scopes, re-run add_account.py for every account.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

CLIENT_SECRETS_PATH = Path(__file__).parent / "credentials.json"


class ReauthorizationNeeded(Exception):
    """An account's token is missing, expired or revoked: run add_account.py for it again."""


def authorize_in_browser() -> Credentials:
    """Open the browser so I can pick an account and grant access. Return its credentials."""
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS_PATH), SCOPES)
    # select_account: always show the account chooser, so I pick which account to add.
    # consent: always issue a new refresh token, even for an account that approved before.
    return flow.run_local_server(port=0, prompt="select_account consent")


def load_credentials(token_path: Path) -> Credentials:
    """Return valid credentials from a saved token, refreshing them if needed.
    Never opens a browser: raises ReauthorizationNeeded instead."""
    if not token_path.exists():
        raise ReauthorizationNeeded(f"no token file at {token_path}")

    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds.valid:
        return creds

    if not creds.refresh_token:
        raise ReauthorizationNeeded("the token has no refresh token")
    try:
        creds.refresh(Request())
    except RefreshError as error:
        # In Testing mode, Google expires refresh tokens after 7 days.
        raise ReauthorizationNeeded("the refresh token expired or was revoked") from error

    # Save the refreshed access token for the next run.
    token_path.write_text(creds.to_json())
    return creds
