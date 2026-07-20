"""Thin wrappers around the OS keyring for the Google OAuth refresh token.

All keyring access for this project flows through here — one place to
swap the backend in tests and one place where the (service, username)
coordinates are used.
"""

import keyring
import keyring.backends.fail
import keyring.backends.null
import keyring.errors

from app.auth.constants import KEYRING_SERVICE, KEYRING_USERNAME


def ensure_backend_available() -> None:
    """Raise CredentialsError if no usable keyring backend is present.

    On headless Linux without a Secret Service daemon the default backend
    is a no-op fail backend.  Detect that early so the error message is
    actionable rather than a silent no-op.
    """
    # Import here to avoid a circular import (google.py → CredentialsError).
    from app.auth.google import CredentialsError

    backend = keyring.get_keyring()
    if isinstance(backend, (keyring.backends.fail.Keyring, keyring.backends.null.Keyring)):
        raise CredentialsError(
            "No OS keyring backend is available "
            f"(backend detected: {type(backend).__name__}). "
            "On Linux, install and start a Secret Service daemon "
            "(e.g. gnome-keyring or kwallet), or install "
            "'keyrings.alt' for a file-based fallback: "
            "uv add keyrings.alt"
        )


def store_refresh_token(token: str) -> None:
    """Write the Google OAuth refresh token to the OS keyring."""
    keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, token)


def load_refresh_token() -> str | None:
    """Return the stored refresh token, or None if not yet set."""
    return keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)


def clear_refresh_token() -> None:
    """Remove the refresh token from the keyring.

    Idempotent — safe to call when nothing is stored.  Any other
    KeyringError (backend unavailable, permission denied, etc.) re-raises
    so the caller sees the real failure.
    """
    try:
        keyring.delete_password(KEYRING_SERVICE, KEYRING_USERNAME)
    except keyring.errors.PasswordDeleteError:
        pass
