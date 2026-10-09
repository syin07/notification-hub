"""Add a Gmail account, or re-authorize one whose token expired."""

from pathlib import Path

import db
from gmail_auth import authorize_in_browser
from gmail_client import build_service, get_profile

TOKENS_DIR = Path(__file__).parent / "tokens"


def main() -> None:
    creds = authorize_in_browser()
    email = get_profile(build_service(creds))["emailAddress"]

    TOKENS_DIR.mkdir(exist_ok=True)
    token_path = TOKENS_DIR / f"{email}.json"
    token_path.write_text(creds.to_json())

    conn = db.connect()
    try:
        db.add_account(conn, email, token_path)
        conn.commit()
    finally:
        conn.close()
    print(f"Authorized {email}. Run sync.py to sync it.")


if __name__ == "__main__":
    main()
