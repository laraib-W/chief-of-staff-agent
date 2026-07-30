"""Unit tests for the pure calendar-event parser (specs.md §3.1.2)."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.providers._internal.calendar_parse import parse_event

USER_TZ = ZoneInfo("Asia/Karachi")  # UTC+05:00, no DST
MAX_DESC = 500


def _timed_event(**overrides) -> dict:
    base = {
        "id": "evt-1",
        "summary": "Standup",
        "start": {"dateTime": "2026-07-30T09:00:00+00:00"},
        "end": {"dateTime": "2026-07-30T09:30:00+00:00"},
    }
    base.update(overrides)
    return base


def test_happy_path_timed_event_preserves_utc_boundaries():
    event = parse_event(_timed_event(), "today", USER_TZ, MAX_DESC)

    assert event.id == "evt-1"
    assert event.summary == "Standup"
    assert event.start == datetime(2026, 7, 30, 9, 0, tzinfo=UTC)
    assert event.end == datetime(2026, 7, 30, 9, 30, tzinfo=UTC)
    assert event.all_day is False
    assert event.is_pending_invite is False
    assert event.attendees == []


def test_all_day_event_normalizes_to_user_tz_midnight():
    raw = _timed_event(start={"date": "2026-07-30"}, end={"date": "2026-07-31"})
    event = parse_event(raw, "today", USER_TZ, MAX_DESC)

    assert event.all_day is True
    # Midnight local (Asia/Karachi = +05:00) → 19:00 UTC previous day.
    assert event.start == datetime(2026, 7, 29, 19, 0, tzinfo=UTC)
    assert event.end == datetime(2026, 7, 30, 19, 0, tzinfo=UTC)


def test_all_day_event_missing_end_defaults_to_next_day():
    """Google can send `end.date == start.date` for a one-day all-day event."""
    raw = _timed_event(start={"date": "2026-07-30"}, end={"date": "2026-07-30"})
    event = parse_event(raw, "today", USER_TZ, MAX_DESC)

    assert event.all_day is True
    assert event.end - event.start == timedelta(days=1)


def test_cross_midnight_event_preserves_span():
    raw = _timed_event(
        start={"dateTime": "2026-07-30T23:00:00+00:00"},
        end={"dateTime": "2026-07-31T01:00:00+00:00"},
    )
    event = parse_event(raw, "today", USER_TZ, MAX_DESC)

    assert event.start.date() != event.end.date()
    assert event.end - event.start == timedelta(hours=2)


def test_alternate_timezone_event_normalized_to_utc():
    raw = _timed_event(
        start={"dateTime": "2026-07-30T10:00:00+09:00"},  # Tokyo
        end={"dateTime": "2026-07-30T11:00:00+09:00"},
    )
    event = parse_event(raw, "today", USER_TZ, MAX_DESC)

    assert event.start.tzinfo == UTC
    assert event.start == datetime(2026, 7, 30, 1, 0, tzinfo=UTC)
    assert event.end == datetime(2026, 7, 30, 2, 0, tzinfo=UTC)


def test_pending_invite_flag_from_needs_action_self_attendee():
    raw = _timed_event(
        attendees=[
            {"email": "colleague@x.com", "responseStatus": "accepted"},
            {"email": "me@x.com", "self": True, "responseStatus": "needsAction"},
        ]
    )
    event = parse_event(raw, "today", USER_TZ, MAX_DESC)

    assert event.response_status == "needsAction"
    assert event.is_pending_invite is True
    assert event.attendees == ["colleague@x.com", "me@x.com"]


def test_accepted_self_attendee_is_not_pending_invite():
    raw = _timed_event(
        attendees=[{"email": "me@x.com", "self": True, "responseStatus": "accepted"}]
    )
    event = parse_event(raw, "today", USER_TZ, MAX_DESC)

    assert event.response_status == "accepted"
    assert event.is_pending_invite is False


def test_lookahead_scope_omits_location_description_organizer():
    raw = _timed_event(
        location="HQ",
        description="Discuss Q3 plans in depth.",
        organizer={"email": "boss@x.com"},
    )
    event = parse_event(raw, "lookahead", USER_TZ, MAX_DESC)

    assert event.location is None
    assert event.description is None
    assert event.organizer is None
    assert event.scope == "lookahead"


def test_today_scope_populates_full_detail():
    raw = _timed_event(
        location="HQ",
        description="Discuss Q3 plans.",
        organizer={"email": "boss@x.com"},
    )
    event = parse_event(raw, "today", USER_TZ, MAX_DESC)

    assert event.location == "HQ"
    assert event.description == "Discuss Q3 plans."
    assert event.organizer == "boss@x.com"


def test_description_truncated_to_config_limit():
    long_body = "x" * 1000
    raw = _timed_event(description=long_body)
    event = parse_event(raw, "today", USER_TZ, max_description_chars=50)

    assert event.description is not None
    assert len(event.description) == 50


def test_description_html_stripped_before_truncation():
    raw = _timed_event(description="<p>Hello <b>world</b></p>")
    event = parse_event(raw, "today", USER_TZ, MAX_DESC)

    assert event.description == "Hello world"


def test_missing_start_or_end_raises():
    raw = _timed_event()
    del raw["end"]
    with pytest.raises(ValueError, match="missing start or end"):
        parse_event(raw, "today", USER_TZ, MAX_DESC)


def test_boundary_without_datetime_or_date_raises():
    raw = _timed_event(start={})
    with pytest.raises(ValueError, match="missing both dateTime and date"):
        parse_event(raw, "today", USER_TZ, MAX_DESC)


def test_dateTime_zulu_suffix_accepted():
    raw = _timed_event(
        start={"dateTime": "2026-07-30T09:00:00Z"},
        end={"dateTime": "2026-07-30T09:30:00Z"},
    )
    event = parse_event(raw, "today", USER_TZ, MAX_DESC)

    assert event.start == datetime(2026, 7, 30, 9, 0, tzinfo=UTC)


def test_no_attendees_leaves_response_status_none():
    raw = _timed_event()  # no attendees key
    event = parse_event(raw, "today", USER_TZ, MAX_DESC)

    assert event.response_status is None
    assert event.is_pending_invite is False
    assert event.attendees == []
