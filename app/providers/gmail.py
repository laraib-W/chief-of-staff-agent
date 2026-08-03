"""Gmail provider — read + send. Send is used only for the daily digest to self.

Sanitization pipeline for incoming mail lives in ``_internal.sanitize`` per
SECURITY.md §3.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

import structlog
from google.auth.exceptions import RefreshError
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.auth import CredentialsError, load_google_credentials
from app.config.loader import GmailConfig
from app.providers._internal.sanitize import sanitize_message
from app.schemas.email import RawEmail

log = structlog.get_logger(__name__)


class GmailClient:
    """Gmail API client (read + send). Pass ``service`` to bypass live auth in tests."""

    def __init__(self, gmail_config: GmailConfig, service=None) -> None:
        self._config = gmail_config
        self._service = service

    def _get_service(self):
        if self._service is None:
            self._service = build("gmail", "v1", credentials=load_google_credentials())
        return self._service

    def fetch_raw(self) -> list[dict]:
        """Return full raw message dicts received within the fetch window."""
        service = self._get_service()
        cutoff = datetime.now(UTC) - timedelta(hours=self._config.fetch_window_hours)
        query = f"after:{int(cutoff.timestamp())}"

        messages_api = service.users().messages()
        listing = messages_api.list(userId="me", q=query).execute()
        ids = [m["id"] for m in listing.get("messages", [])]
        log.info("gmail.fetch_raw", count=len(ids))

        return [
            messages_api.get(userId="me", id=mid, format="full").execute()
            for mid in ids
        ]

    def fetch_emails(self) -> tuple[list[RawEmail], str | None]:
        """Fetch and sanitize email. Returns (emails, error_or_None)."""
        try:
            raw = self.fetch_raw()
        except (CredentialsError, RefreshError, HttpError, OSError) as exc:
            log.warning("gmail.fetch_failed", error=str(exc))
            return [], f"Gmail fetch failed: {exc}"

        emails: list[RawEmail] = []
        last_error: str | None = None
        for msg in raw:
            try:
                emails.append(sanitize_message(msg, self._config.max_body_chars))
            except Exception as exc:  # noqa: BLE001 — skip one bad message
                last_error = str(exc)
                log.warning(
                    "gmail.sanitize_skip",
                    message_id=msg.get("id") if isinstance(msg, dict) else None,
                    error=last_error,
                )

        if raw and not emails:
            error = f"All {len(raw)} fetched messages failed to sanitize: {last_error}"
            log.warning("gmail.all_messages_failed", count=len(raw))
            return [], error

        return emails, None

    def send_html(
        self, *, to: str, subject: str, html_body: str
    ) -> tuple[str | None, str | None]:
        """Send an HTML email via ``users().messages().send``.

        Returns ``(message_id, None)`` on success, ``(None, error)`` on
        failure. Callers route the error string to state so the run still
        completes.
        """
        message = EmailMessage()
        message["To"] = to
        message["Subject"] = subject
        message.set_content(
            "This digest is HTML-only. Open in a client that renders HTML."
        )
        message.add_alternative(html_body, subtype="html")

        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")

        try:
            service = self._get_service()
            sent = (
                service.users()
                .messages()
                .send(userId="me", body={"raw": raw})
                .execute()
            )
        except (CredentialsError, RefreshError, HttpError, OSError) as exc:
            log.warning("gmail.send_failed", error=str(exc))
            return None, f"Gmail send failed: {exc}"

        message_id = sent.get("id")
        log.info("gmail.send_ok", message_id=message_id, to=to)
        return message_id, None
