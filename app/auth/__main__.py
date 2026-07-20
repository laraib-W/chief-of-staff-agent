"""CLI entry point: python -m app.auth --setup | --reauth

No business logic lives here — this module only handles argument parsing,
user-visible output, and top-level error handling.  All credential work
delegates to app.auth.google and app.auth.keyring_store.
"""

import argparse
import sys

import structlog
from dotenv import load_dotenv

from app.auth.constants import KEYRING_SERVICE, KEYRING_USERNAME
from app.auth.google import (
    CredentialsError,
    _read_google_client_from_env,
    run_setup_flow,
)
from app.auth.keyring_store import (
    ensure_backend_available,
    load_refresh_token,
    store_refresh_token,
)

log = structlog.get_logger("app.auth")


def _cmd_setup() -> None:
    """Run the one-time OAuth browser flow and store the refresh token."""
    ensure_backend_available()

    if load_refresh_token() is not None:
        print(
            "Already authenticated. Use --reauth to replace the stored token.",
            file=sys.stdout,
        )
        return

    log.info("auth_setup_started")

    client_id, client_secret = _read_google_client_from_env()
    token = run_setup_flow(client_id, client_secret)
    store_refresh_token(token)

    log.info("auth_token_stored", service=KEYRING_SERVICE, username=KEYRING_USERNAME)
    print(
        f"Refresh token stored in keyring "
        f"(service={KEYRING_SERVICE!r}, account={KEYRING_USERNAME!r}).",
        file=sys.stdout,
    )


def _cmd_reauth() -> None:
    """Re-run the OAuth flow and overwrite the stored token.

    The flow runs first; the token is only overwritten on success.
    If the user aborts the browser flow, the original token is preserved.
    """
    ensure_backend_available()

    log.info("auth_reauth_started")

    client_id, client_secret = _read_google_client_from_env()
    token = run_setup_flow(client_id, client_secret)
    store_refresh_token(token)

    log.info("auth_reauth_stored", service=KEYRING_SERVICE, username=KEYRING_USERNAME)
    print(
        f"Refresh token updated in keyring "
        f"(service={KEYRING_SERVICE!r}, account={KEYRING_USERNAME!r}).",
        file=sys.stdout,
    )


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(
        prog="python -m app.auth",
        description="One-time Google OAuth setup for the Chief-of-Staff Agent.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--setup",
        action="store_true",
        help="Run the browser OAuth flow and store the refresh token.",
    )
    group.add_argument(
        "--reauth",
        action="store_true",
        help="Re-run the OAuth flow and overwrite the stored token.",
    )
    args = parser.parse_args()

    try:
        if args.setup:
            _cmd_setup()
        else:
            _cmd_reauth()
    except CredentialsError as exc:
        log.error("auth_credentials_missing", reason=str(exc))
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
