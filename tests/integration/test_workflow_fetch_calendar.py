"""Integration test: fetch_calendar wired into the compiled graph (specs.md §3, §4)."""

from datetime import UTC, datetime

from app.graph.workflow import build_graph
from app.providers import calendar, gmail
from app.schemas.calendar import CalendarEvent
from app.storage import memory as memory_store
from tests.conftest import make_config


def test_graph_invoke_runs_fetch_calendar_node(monkeypatch, tmp_path):
    fake_event = CalendarEvent(
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
    monkeypatch.setattr(
        calendar.CalendarClient, "fetch_events", lambda self: ([fake_event], None)
    )
    monkeypatch.setattr(gmail.GmailClient, "fetch_emails", lambda self: ([], None))

    memory_path = tmp_path / "memory.db"
    memory_store.bootstrap(memory_path)

    graph = build_graph(make_config())
    final_state = graph.invoke(
        {"errors": {}},
        config={"configurable": {"memory_db_path": memory_path}},
    )

    assert final_state["calendar_events"] == [fake_event]
    assert final_state["errors"]["calendar"] is None
