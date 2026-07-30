"""Pure Google Calendar event parsing — no I/O (specs.md §3.1.2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from app.providers._internal.sanitize import strip_html, truncate
from app.schemas.calendar import CalendarEvent

Scope = Literal["today", "lookahead"]


def _parse_all_day_boundary(date_str: str, user_tz: ZoneInfo) -> datetime:
    """Turn a Google `start.date` / `end.date` value into an aware UTC datetime."""
    naive_midnight = datetime.strptime(date_str, "%Y-%m-%d")
    local_midnight = naive_midnight.replace(tzinfo=user_tz)
    return local_midnight.astimezone(UTC)


def _parse_datetime(dt_str: str) -> datetime:
    """Turn a Google `start.dateTime` value into an aware UTC datetime.

    Google sends RFC 3339 with either a `Z` suffix or a numeric offset —
    `fromisoformat` accepts both on 3.11+ (it also expands `Z` since 3.11).
    """
    parsed = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"dateTime missing timezone offset: {dt_str!r}")
    return parsed.astimezone(UTC)


def _parse_boundary(field: dict, user_tz: ZoneInfo) -> tuple[datetime, bool]:
    """Return (utc_datetime, all_day) for a Google start/end block."""
    if "dateTime" in field:
        return _parse_datetime(field["dateTime"]), False
    if "date" in field:
        return _parse_all_day_boundary(field["date"], user_tz), True
    raise ValueError("event boundary missing both dateTime and date")


def _extract_attendees_and_status(raw: dict) -> tuple[list[str], str | None]:
    """Return (attendee emails, self attendee's responseStatus or None)."""
    attendees_raw = raw.get("attendees") or []
    emails = [a["email"] for a in attendees_raw if a.get("email")]
    self_status = next(
        (a.get("responseStatus") for a in attendees_raw if a.get("self")),
        None,
    )
    return emails, self_status


def parse_event(
    raw: dict,
    scope: Scope,
    user_tz: ZoneInfo,
    max_description_chars: int,
) -> CalendarEvent:
    """Turn a Google Calendar API event dict into a validated CalendarEvent.

    All timestamps are normalized to UTC. For look-ahead scope, location /
    description / organizer are left as None so state doesn't carry
    summary-scope fields the spec forbids.
    """
    if "start" not in raw or "end" not in raw:
        raise ValueError("event missing start or end")

    start, start_all_day = _parse_boundary(raw["start"], user_tz)
    end, end_all_day = _parse_boundary(raw["end"], user_tz)
    all_day = start_all_day and end_all_day
    if all_day and end == start:
        end = start + timedelta(days=1)

    attendees, response_status = _extract_attendees_and_status(raw)

    location: str | None = None
    description: str | None = None
    organizer: str | None = None
    if scope == "today":
        location = raw.get("location") or None
        organizer = (raw.get("organizer") or {}).get("email") or None
        raw_description = raw.get("description") or ""
        if raw_description:
            description = truncate(strip_html(raw_description), max_description_chars)
            description = description or None

    return CalendarEvent(
        id=raw.get("id", ""),
        summary=raw.get("summary", "") or "",
        start=start,
        end=end,
        all_day=all_day,
        attendees=attendees,
        response_status=response_status,
        is_pending_invite=response_status == "needsAction",
        scope=scope,
        location=location,
        description=description,
        organizer=organizer,
    )
