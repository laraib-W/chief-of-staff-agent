"""Gmail provider — read-only, includes the sanitization pipeline (SECURITY.md §3)."""

from __future__ import annotations

import glob
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import keyring
import structlog
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.config.loader import Config, GmailConfig
from app.providers._sanitize import sanitize_message
from app.schemas.email import RawEmail

log = structlog.get_logger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
KEYRING_SERVICE = "cos-agent"
KEYRING_USERNAME = "google_refresh_token"
TOKEN_URI = "https://oauth2.googleapis.com/token"  # noqa: S105


class GmailAuthError(Exception):
    """Raised when Gmail credentials cannot be loaded."""


def _load_credentials() -> Credentials:
    refresh_token = keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
    if not refresh_token:
        raise GmailAuthError(
            "No Gmail refresh token in keyring. "
            "Run `python -m app.auth` to authenticate."
        )
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        client_id=os.environ["GOOGLE_OAUTH_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
        token_uri=TOKEN_URI,
        scopes=SCOPES,
    )
    creds.refresh(Request())
    return creds


class GmailClient:
    """Read-only Gmail API client. Pass ``service`` to bypass live auth in tests."""

    def __init__(self, gmail_config: GmailConfig, service=None) -> None:
        self._config = gmail_config
        self._service = service

    def _get_service(self):
        if self._service is None:
            self._service = build("gmail", "v1", credentials=_load_credentials())
        return self._service

    def fetch_raw(self) -> list[dict]:
        """Return full raw message dicts received within the fetch window."""
        service = self._get_service()
        cutoff = datetime.now(UTC) - timedelta(
            hours=self._config.fetch_window_hours
        )
        query = f"after:{int(cutoff.timestamp())}"

        messages_api = service.users().messages()
        listing = messages_api.list(userId="me", q=query).execute()
        ids = [m["id"] for m in listing.get("messages", [])]
        log.info("gmail.fetch_raw", count=len(ids))

        return [
            messages_api.get(userId="me", id=mid, format="full").execute()
            for mid in ids
        ]


FIXTURE_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures"


def load_raw_from_fixture(date: str) -> list[dict]:
    path = FIXTURE_DIR / f"gmail_{date}.json"
    return json.loads(path.read_text())


def _latest_fixture() -> list[dict]:
    matches = sorted(glob.glob(str(FIXTURE_DIR / "gmail_*.json")))
    if not matches:
        raise FileNotFoundError("No gmail_*.json fixture found for replay.")
    return json.loads(Path(matches[-1]).read_text())


def record_raw(raw: list[dict], date: str) -> None:
    (FIXTURE_DIR / f"gmail_{date}.json").write_text(
        json.dumps(raw, indent=2)
    )


def fetch_emails(
    config: Config,
    *,
    replay: bool = False,
    record: bool = False,
) -> tuple[list[RawEmail], str | None]:
    """Fetch and sanitize email. Returns (emails, error_or_None)."""
    try:
        if replay:
            raw = _latest_fixture()
        else:
            raw = GmailClient(config.gmail).fetch_raw()
            if record:
                today = datetime.now(UTC).strftime("%Y-%m-%d")
                record_raw(raw, today)
    except Exception as exc:  # noqa: BLE001 — degrade gracefully per §3.1.1
        log.warning("gmail.fetch_failed", error=str(exc))
        return [], f"Gmail fetch failed: {exc}"

    emails: list[RawEmail] = []
    for msg in raw:
        try:
            emails.append(
                sanitize_message(msg, config.gmail.max_body_chars)
            )
        except Exception as exc:  # noqa: BLE001 — skip one bad message
            log.warning(
                "gmail.sanitize_skip",
                message_id=msg.get("id") if isinstance(msg, dict) else None,
                error=str(exc),
            )
    return emails, None
