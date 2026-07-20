"""Google OAuth credential management.

Public surface (re-exported via app.auth):
  - CredentialsError
  - load_google_credentials

Internal helpers used only by __main__:
  - run_setup_flow
  - _read_google_client_from_env
"""

import os

import structlog
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from app.auth.constants import (
    GOOGLE_AUTH_URI,
    GOOGLE_TOKEN_URI,
    KEYRING_SERVICE,
    KEYRING_USERNAME,
    SCOPES,
)
from app.auth.keyring_store import load_refresh_token

log = structlog.get_logger(__name__)


class CredentialsError(Exception):
    """Raised for any missing or invalid credential.

    The message always names what is missing and how to fix it, so it can
    be surfaced directly to the user without wrapping.
    """


def _read_google_client_from_env() -> tuple[str, str]:
    """Read GOOGLE_OAUTH_CLIENT_ID and _SECRET from the environment.

    Raises CredentialsError naming the first missing variable and pointing
    to where it can be obtained.
    """
    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
    if not client_id:
        raise CredentialsError(
            "GOOGLE_OAUTH_CLIENT_ID is not set. "
            "Create a Desktop-app OAuth 2.0 client at "
            "https://console.cloud.google.com → APIs & Services → Credentials, "
            "then add GOOGLE_OAUTH_CLIENT_ID to your .env file."
        )

    client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
    if not client_secret:
        raise CredentialsError(
            "GOOGLE_OAUTH_CLIENT_SECRET is not set. "
            "Download the client secret from the same OAuth 2.0 client at "
            "https://console.cloud.google.com → APIs & Services → Credentials, "
            "then add GOOGLE_OAUTH_CLIENT_SECRET to your .env file."
        )

    return client_id, client_secret


def run_setup_flow(client_id: str, client_secret: str) -> str:
    """Run the Google OAuth installed-app browser flow and return the refresh token.

    Takes credentials as explicit parameters (not env reads) so this
    function can be unit-tested by injecting values directly.

    Raises CredentialsError if the flow completes without a refresh token,
    which happens when the OAuth client is not of type 'Desktop app'.
    """
    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": GOOGLE_AUTH_URI,
            "token_uri": GOOGLE_TOKEN_URI,
            "redirect_uris": ["http://localhost"],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

    log.info("auth_flow_completed", service=KEYRING_SERVICE, username=KEYRING_USERNAME)

    if not creds.refresh_token:
        raise CredentialsError(
            "OAuth flow completed but returned no refresh token. "
            "Ensure the OAuth client in Google Cloud Console is type "
            "'Desktop app', then re-run: python -m app.auth --setup"
        )

    return creds.refresh_token


def load_google_credentials() -> Credentials:
    """Build a Google Credentials object from the OS keyring and environment.

    Called by Phase 1+ providers (fetch_gmail, fetch_calendar) at runtime.
    Raises CredentialsError — providers catch this and route to errors[sensor].
    """
    client_id, client_secret = _read_google_client_from_env()

    refresh_token = load_refresh_token()
    if refresh_token is None:
        log.error(
            "auth_credentials_missing",
            reason="no refresh token in keyring",
            fix="python -m app.auth --setup",
        )
        raise CredentialsError(
            "No Google OAuth refresh token found in the keyring. "
            "Run: python -m app.auth --setup"
        )

    return Credentials(
        token=None,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri=GOOGLE_TOKEN_URI,
        scopes=SCOPES,
    )
