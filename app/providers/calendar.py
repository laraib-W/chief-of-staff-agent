"""Google Calendar provider — read-only (specs.md §3.1.2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import httplib2
import structlog
from google.auth.exceptions import RefreshError
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.auth import CredentialsError, load_google_credentials
from app.config.loader import Config
from app.providers._internal.calendar_parse import parse_event
from app.schemas.calendar import CalendarEvent

log = structlog.get_logger(__name__)

LOOKAHEAD_DAYS = 7
PRIMARY_CALENDAR = "primary"
MAX_RESULTS_PER_WINDOW = 250
# Bounds the socket read on each Calendar API request. httplib2's default
# is unbounded; we saw `OSError: timed out` on real runs where the OS's
# own short default was tripping. 120 s is generous but real: on some
# corporate VPN / TLS-inspecting proxy paths a single API call to
# www.googleapis.com has been observed at 45+ s. Fast networks are
# unaffected — the timeout only trips when the request is genuinely stuck.
_HTTP_TIMEOUT_SECONDS = 120


class CalendarClient:
    """Read-only Google Calendar API client.

    Pass ``service`` to bypass live auth in tests.
    """

    def __init__(self, config: Config, service=None) -> None:
        self._config = config
        self._service = service

    def _get_service(self):
        if self._service is None:
            authed_http = AuthorizedHttp(
                load_google_credentials(),
                http=httplib2.Http(timeout=_HTTP_TIMEOUT_SECONDS),
            )
            self._service = build("calendar", "v3", http=authed_http)
        return self._service

    def _fetch_raw_window(
        self, time_min_utc: datetime, time_max_utc: datetime
    ) -> list[dict]:
        """Return the raw Google Calendar events in [time_min_utc, time_max_utc)."""
        response = (
            self._get_service()
            .events()
            .list(
                calendarId=PRIMARY_CALENDAR,
                timeMin=time_min_utc.isoformat(),
                timeMax=time_max_utc.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=MAX_RESULTS_PER_WINDOW,
            )
            .execute()
        )
        return response.get("items", [])

    def fetch_events(self) -> tuple[list[CalendarEvent], str | None]:
        """Fetch today + 7-day look-ahead. Returns (events, error_or_None)."""
        try:
            tz = ZoneInfo(self._config.identity.timezone)
            today_start_local = datetime.now(tz).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            today_end_local = today_start_local + timedelta(days=1)
            lookahead_end_local = today_end_local + timedelta(days=LOOKAHEAD_DAYS)

            today_raw = self._fetch_raw_window(
                today_start_local.astimezone(UTC), today_end_local.astimezone(UTC)
            )
            lookahead_raw = self._fetch_raw_window(
                today_end_local.astimezone(UTC), lookahead_end_local.astimezone(UTC)
            )
        except (CredentialsError, RefreshError, HttpError, OSError) as exc:
            log.warning("calendar.fetch_failed", error=str(exc))
            return [], f"Calendar fetch failed: {exc}"

        max_desc = self._config.calendar.max_description_chars
        events: list[CalendarEvent] = []
        last_error: str | None = None
        for scope, raws in (("today", today_raw), ("lookahead", lookahead_raw)):
            for raw in raws:
                try:
                    events.append(parse_event(raw, scope, tz, max_desc))
                except (KeyError, ValueError, TypeError) as exc:
                    last_error = str(exc)
                    log.warning(
                        "calendar.parse_skip",
                        event_id=raw.get("id") if isinstance(raw, dict) else None,
                        error=last_error,
                    )

        total = len(today_raw) + len(lookahead_raw)
        if total and not events:
            error = f"All {total} fetched events failed to parse: {last_error}"
            log.warning("calendar.all_events_failed", count=total)
            return [], error

        return events, None
