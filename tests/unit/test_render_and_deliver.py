"""Unit tests for the render_and_deliver node (specs.md §3.4.1)."""

from datetime import UTC, datetime, timedelta

import pytest

from app.nodes.render_and_deliver import render_and_deliver_node
from app.schemas.calendar import CalendarEvent
from app.schemas.digest import EvidenceRef, Priority
from app.schemas.email import RawEmail
from app.schemas.plane import PersonHealth
from app.storage import memory as memory_store
from tests.conftest import make_config


def _cal_event(**overrides) -> CalendarEvent:
    start = overrides.pop("start", datetime.now(tz=UTC).replace(hour=10, minute=0))
    defaults = {
        "id": "e1",
        "summary": "Standup",
        "start": start,
        "end": start + timedelta(minutes=30),
        "all_day": False,
        "attendees": [],
        "response_status": "accepted",
        "is_pending_invite": False,
        "scope": "today",
        "location": None,
        "description": None,
        "organizer": None,
    }
    defaults.update(overrides)
    return CalendarEvent(**defaults)


def _person(**overrides) -> PersonHealth:
    defaults = {
        "person": "Alice",
        "person_id": "u1",
        "status": "on_track",
        "in_progress_count": 2,
        "overdue_items": [],
        "inactive_items": [],
        "on_time_rate": None,
        "completions_no_due_date": 0,
        "last_activity": datetime.now(tz=UTC),
        "summary": "2 in progress.",
    }
    defaults.update(overrides)
    return PersonHealth(**defaults)


def _state(**overrides) -> dict:
    defaults = {
        "errors": {"gmail": None, "calendar": None, "plane": None},
        "calendar_events": [],
        "team_health": [],
        "top_priorities": [],
        "emails": [],
        "email_actions": [],
    }
    defaults.update(overrides)
    return defaults


def _configurable(**overrides) -> dict:
    defaults = {"dry_run": True, "run_id": 1, "memory_db_path": None}
    defaults.update(overrides)
    return {"configurable": defaults}


def _recording_sender():
    calls: list[tuple[str, str]] = []

    def _send(subject: str, html: str) -> tuple[str | None, str | None]:
        calls.append((subject, html))
        return "msg-123", None

    return _send, calls


# ─── rendering ─────────────────────────────────────────────────────────────


def test_renders_team_and_calendar_sections():
    cfg = make_config()
    node = render_and_deliver_node(cfg, sender=_recording_sender()[0])

    state = _state(
        calendar_events=[_cal_event(summary="Design review")],
        team_health=[_person(person="Alice")],
    )

    out = node(state, _configurable())

    html = out["digest_html"]
    assert "Design review" in html
    assert "Alice" in html
    assert "Today's schedule" in html
    assert "Team board" in html
    assert "Needs your reply" in html
    assert "Connections spotted" in html


def test_priorities_render_with_evidence():
    cfg = make_config()
    node = render_and_deliver_node(cfg, sender=_recording_sender()[0])

    state = _state(
        top_priorities=[
            Priority(
                rank=1,
                headline="PROJ-1 is blocking the release",
                evidence=[EvidenceRef(source="plane", ref_id="PROJ-1", label="Bug X")],
                source_types=["plane"],
                connection=None,
            )
        ]
    )

    html = node(state, _configurable())["digest_html"]

    assert "PROJ-1 is blocking the release" in html
    assert "plane:PROJ-1" in html


def test_partial_failure_banner_lists_every_broken_source():
    cfg = make_config()
    node = render_and_deliver_node(cfg, sender=_recording_sender()[0])

    state = _state(
        errors={
            "gmail": "token expired",
            "calendar": "quota exceeded",
            "plane": None,
        }
    )

    html = node(state, _configurable())["digest_html"]

    assert "Gmail" in html
    assert "Calendar" in html
    assert "unavailable today" in html
    # Per-section fallbacks appear too — the section still renders a status line.
    assert "Gmail unavailable today." in html
    assert "Calendar data unavailable today." in html


def test_no_banner_when_all_sensors_healthy():
    cfg = make_config()
    node = render_and_deliver_node(cfg, sender=_recording_sender()[0])

    html = node(_state(), _configurable())["digest_html"]

    # Banner uses a warning glyph; absence proves the block wasn't rendered.
    assert "⚠" not in html


def test_needs_reply_falls_back_when_classification_pending():
    """Until classify_emails lands, the section should flag classification pending
    rather than silently showing nothing."""
    cfg = make_config()
    node = render_and_deliver_node(cfg, sender=_recording_sender()[0])

    state = _state(
        emails=[
            RawEmail(
                id="m1",
                thread_id="t1",
                sender="a@x",
                subject="Hi",
                clean_body="hey",
                date=datetime.now(tz=UTC),
            )
        ]
    )

    html = node(state, _configurable())["digest_html"]

    assert "Email classification not yet enabled" in html
    assert "1 email(s)" in html


# ─── delivery vs dry-run ───────────────────────────────────────────────────


def test_dry_run_skips_sender():
    cfg = make_config()
    send, calls = _recording_sender()
    node = render_and_deliver_node(cfg, sender=send)

    out = node(_state(), _configurable(dry_run=True))

    assert calls == []
    assert "digest_html" in out


def test_live_run_invokes_sender_with_subject_and_html():
    cfg = make_config()
    send, calls = _recording_sender()
    node = render_and_deliver_node(cfg, sender=send)

    node(_state(), _configurable(dry_run=False))

    assert len(calls) == 1
    subject, html = calls[0]
    assert subject.startswith("Morning digest — ")
    assert "<html" in html.lower()


def test_send_error_routes_to_errors_and_still_returns_html():
    def failing(subject, html):
        return None, "network unreachable"

    cfg = make_config()
    node = render_and_deliver_node(cfg, sender=failing)

    out = node(_state(), _configurable(dry_run=False))

    assert out["errors"]["delivery"] == "network unreachable"
    assert out["digest_html"]


def test_successful_send_writes_digest_log(tmp_path):
    memory_db = tmp_path / "memory.db"
    memory_store.bootstrap(memory_db)

    cfg = make_config()
    send, _ = _recording_sender()
    node = render_and_deliver_node(cfg, sender=send)

    node(
        _state(),
        _configurable(dry_run=False, memory_db_path=memory_db, run_id=42),
    )

    with memory_store.connect(memory_db) as conn:
        rows = conn.execute("SELECT run_id, digest_html FROM digest_log").fetchall()
    assert len(rows) == 1
    assert rows[0]["run_id"] == 42
    assert "<html" in rows[0]["digest_html"].lower()


def test_failed_send_does_not_write_digest_log(tmp_path):
    memory_db = tmp_path / "memory.db"
    memory_store.bootstrap(memory_db)

    def failing(subject, html):
        return None, "unauthorized"

    cfg = make_config()
    node = render_and_deliver_node(cfg, sender=failing)

    node(
        _state(),
        _configurable(dry_run=False, memory_db_path=memory_db, run_id=42),
    )

    with memory_store.connect(memory_db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM digest_log").fetchone()[0]
    assert count == 0


def test_dry_run_does_not_write_digest_log(tmp_path):
    memory_db = tmp_path / "memory.db"
    memory_store.bootstrap(memory_db)

    cfg = make_config()
    send, _ = _recording_sender()
    node = render_and_deliver_node(cfg, sender=send)

    node(
        _state(),
        _configurable(dry_run=True, memory_db_path=memory_db, run_id=42),
    )

    with memory_store.connect(memory_db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM digest_log").fetchone()[0]
    assert count == 0


@pytest.mark.parametrize(
    ("errors", "expected_fragment"),
    [
        ({"gmail": "oops"}, "Gmail data unavailable today."),
        (
            {"gmail": "oops", "plane": "boom"},
            "Gmail and Plane data unavailable today.",
        ),
        (
            {"plane": "boom", "calendar": "meh", "gmail": "oops"},
            "Gmail, Calendar and Plane data unavailable today.",
        ),
    ],
)
def test_failure_banner_wording(errors, expected_fragment):
    cfg = make_config()
    send, _ = _recording_sender()
    node = render_and_deliver_node(cfg, sender=send)

    html = node(_state(errors=errors), _configurable())["digest_html"]

    assert expected_fragment in html
