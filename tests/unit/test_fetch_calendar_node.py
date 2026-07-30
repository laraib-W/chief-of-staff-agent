"""Unit tests for the fetch_calendar sensor node (specs.md §3.1.2)."""

from datetime import UTC, datetime

from app.nodes.fetch_calendar import fetch_calendar_node
from app.providers import calendar
from app.schemas.calendar import CalendarEvent
from tests.conftest import make_config


def _fake_event() -> CalendarEvent:
    return CalendarEvent(
        id="e1",
        summary="Standup",
        start=datetime(2026, 7, 30, 9, 0, tzinfo=UTC),
        end=datetime(2026, 7, 30, 9, 30, tzinfo=UTC),
        all_day=False,
        attendees=["a@b.com"],
        response_status="accepted",
        is_pending_invite=False,
        scope="today",
    )


def test_fetch_calendar_node_writes_events_and_clears_error(monkeypatch):
    fake = _fake_event()
    monkeypatch.setattr(
        calendar.CalendarClient, "fetch_events", lambda self: ([fake], None)
    )

    node = fetch_calendar_node(make_config())
    result = node({"errors": {}})

    assert result["calendar_events"] == [fake]
    assert result["errors"]["calendar"] is None


def test_fetch_calendar_node_writes_error_and_empty_list_on_failure(monkeypatch):
    monkeypatch.setattr(
        calendar.CalendarClient,
        "fetch_events",
        lambda self: ([], "Calendar fetch failed: boom"),
    )

    node = fetch_calendar_node(make_config())
    result = node({"errors": {}})

    assert result["calendar_events"] == []
    assert result["errors"]["calendar"] == "Calendar fetch failed: boom"


def test_fetch_calendar_node_preserves_other_error_keys(monkeypatch):
    monkeypatch.setattr(
        calendar.CalendarClient, "fetch_events", lambda self: ([], None)
    )

    node = fetch_calendar_node(make_config())
    result = node({"errors": {"gmail": "gmail down"}})

    assert result["errors"] == {"gmail": "gmail down", "calendar": None}
