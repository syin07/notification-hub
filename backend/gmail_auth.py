"""OAuth 2.0 for Gmail: load, refresh or create credentials for one account."""

from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# If you change the scopes, delete the token files so every account re-authorizes.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

CLIENT_SECRETS_PATH = Path(__file__).parent / "credentials.json"


def get_credentials(token_path: Path) -> Credentials:
    """Return valid credentials, refreshing them or re-authorizing in the browser if needed."""
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError:
            print("Refresh token expired or revoked; please re-authorize in the browser.")
            creds = None

    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS_PATH), SCOPES)
        creds = flow.run_local_server(port=0)

    # Save the credentials for the next run.
    token_path.write_text(creds.to_json())
    return creds
