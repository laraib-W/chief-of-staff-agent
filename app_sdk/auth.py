"""Google OAuth for the SDK branch's deterministic send path.

The Claude Code agent reads Gmail via an MCP server; this module owns
the one write path — sending the finished morning digest via the Gmail
API from `app_sdk/run.py`. Scope is intentionally narrow (`gmail.send`
only) because reads live behind the MCP.

Self-contained: does not import from `app/*`. The two branches are
independent implementations of the same product and each maintain
their own credential store. This module uses a distinct keyring
service name (`chief-of-staff-agent-sdk`) so the two token caches
never collide.

Usage:
    python -m app_sdk.auth --setup     # first-time browser consent
    python -m app_sdk.auth --reauth    # replace stored refresh token

    # In code:
    from app_sdk.auth import load_google_credentials
    creds = load_google_credentials()
"""

from __future__ import annotations

import argparse
import os
import sys

import keyring
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

KEYRING_SERVICE = "chief-of-staff-agent-sdk"
KEYRING_USERNAME = "google_oauth_refresh_token"

# Narrow scope: reads happen via the Gmail MCP server, which runs its
# own OAuth. This module's credentials exist only to send the digest.
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"  # noqa: S105

# Fixed loopback port for this flow's redirect. One Web-application OAuth
# client serves all three consumers (gmail MCP, gcal MCP, and this send
# path), so every redirect URI it uses must be pre-registered on that
# client — a random port would fail `redirect_uri_mismatch`. The MCP
# servers use http://localhost:8765/callback; this flow uses
# http://localhost:8766/ (google-auth-oauthlib builds the URI as
# "http://{host}:{port}/" — bare slash, no path). Register both.
SEND_CALLBACK_PORT = 8766


class CredentialsError(Exception):
    """Raised when credentials are missing or unusable.

    Every message names the specific gap and the remediation command,
    so the caller can surface it verbatim to the user.
    """


def _read_client_from_env() -> tuple[str, str]:
    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
    if not client_id:
        raise CredentialsError(
            "GOOGLE_OAUTH_CLIENT_ID is not set. Create a Web-application "
            "OAuth client at https://console.cloud.google.com → APIs & "
            "Services → Credentials (the same client the gmail/gcal MCP "
            "servers use), then add GOOGLE_OAUTH_CLIENT_ID to your .env "
            "file. See docs/mcp-servers.md for the redirect URIs it needs."
        )
    client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
    if not client_secret:
        raise CredentialsError(
            "GOOGLE_OAUTH_CLIENT_SECRET is not set. Add it to your .env "
            "file — it's the client secret from the same OAuth client."
        )
    return client_id, client_secret


def _store_refresh_token(token: str) -> None:
    keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, token)


def _load_refresh_token() -> str | None:
    return keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)


def _run_browser_flow(
    client_id: str, client_secret: str, login_hint: str | None = None
) -> str:
    """Run the installed-app OAuth flow and return the refresh token.

    `login_hint` is forwarded to Google's authorization URL so the consent
    screen pre-selects that account — helpful when the default browser is
    signed into a different account than the one that should authorize.
    """
    # Key is "web" because this is a Web-application client — the same one
    # the gmail/gcal MCP servers use. google-auth-oauthlib accepts either
    # "web" or "installed"; naming it correctly keeps the Console client
    # type and this config in agreement.
    client_config = {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": GOOGLE_AUTH_URI,
            "token_uri": GOOGLE_TOKEN_URI,
            "redirect_uris": [f"http://localhost:{SEND_CALLBACK_PORT}/"],
        }
    }
    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    kwargs = {"login_hint": login_hint} if login_hint else {}
    # `prompt="consent"` forces Google to re-issue a refresh token. Without
    # it, a second authorization of an already-granted client returns an
    # access token only, and the keyring entry would be unrefreshable.
    creds = flow.run_local_server(port=SEND_CALLBACK_PORT, prompt="consent", **kwargs)
    if not creds.refresh_token:
        raise CredentialsError(
            "OAuth flow completed without a refresh token. Confirm the "
            "consent screen was fully accepted, and that "
            f"http://localhost:{SEND_CALLBACK_PORT}/ is registered as an "
            "authorized redirect URI on the OAuth client."
        )
    return creds.refresh_token


def load_google_credentials() -> Credentials:
    """Return refreshed Google credentials for the gmail.send scope.

    Raises CredentialsError if setup hasn't been run — the message points
    at `python -m app_sdk.auth --setup`.
    """
    refresh_token = _load_refresh_token()
    if refresh_token is None:
        raise CredentialsError(
            "No Google refresh token in keyring. Run "
            "`python -m app_sdk.auth --setup` once to authorize the send "
            "path."
        )
    client_id, client_secret = _read_client_from_env()
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=GOOGLE_TOKEN_URI,
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )
    creds.refresh(Request())
    return creds


def _cmd_setup(force: bool, login_hint: str | None) -> None:
    if not force and _load_refresh_token() is not None:
        print("Already authenticated. Use --reauth to replace the stored token.")
        return
    client_id, client_secret = _read_client_from_env()
    token = _run_browser_flow(client_id, client_secret, login_hint=login_hint)
    _store_refresh_token(token)
    print(
        f"Refresh token stored in keyring "
        f"(service={KEYRING_SERVICE!r}, account={KEYRING_USERNAME!r})."
    )


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(
        prog="python -m app_sdk.auth",
        description="Google OAuth setup for the SDK branch's send path.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--setup",
        action="store_true",
        help="Run the browser OAuth flow if no token is stored.",
    )
    group.add_argument(
        "--reauth",
        action="store_true",
        help="Re-run the OAuth flow and overwrite the stored token.",
    )
    parser.add_argument(
        "--account",
        default=None,
        help=(
            "Google account email to pre-select on the consent screen "
            "(login_hint). Useful when your default browser is signed "
            "into a different account."
        ),
    )
    args = parser.parse_args()

    try:
        _cmd_setup(force=args.reauth, login_hint=args.account)
    except CredentialsError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
