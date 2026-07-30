"""Unit tests for CalendarClient (specs.md §3.1.2)."""

from app.auth import CredentialsError
from app.providers import calendar
from tests.conftest import make_config


class _FakeExec:
    def __init__(self, value, raises: Exception | None = None):
        self._value = value
        self._raises = raises

    def execute(self):
        if self._raises is not None:
            raise self._raises
        return self._value


class _FakeEvents:
    """Returns the next queued response per `.list().execute()` call."""

    def __init__(self, responses: list):
        self._responses = list(responses)

    def list(self, **kwargs):
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            return _FakeExec(None, raises=response)
        return _FakeExec(response)


class _FakeService:
    def __init__(self, responses: list):
        self._events = _FakeEvents(responses)

    def events(self):
        return self._events


def _timed_event(event_id: str, dt: str, end_dt: str) -> dict:
    return {
        "id": event_id,
        "summary": "Meeting",
        "start": {"dateTime": dt},
        "end": {"dateTime": end_dt},
    }


def test_fetch_events_returns_both_scopes():
    today = {
        "items": [
            _timed_event(
                "t1", "2026-07-30T09:00:00+00:00", "2026-07-30T09:30:00+00:00"
            ),
            _timed_event(
                "t2", "2026-07-30T10:00:00+00:00", "2026-07-30T10:30:00+00:00"
            ),
        ]
    }
    lookahead = {
        "items": [
            _timed_event(
                "l1", "2026-07-31T09:00:00+00:00", "2026-07-31T09:30:00+00:00"
            ),
            _timed_event(
                "l2", "2026-08-01T09:00:00+00:00", "2026-08-01T09:30:00+00:00"
            ),
            _timed_event(
                "l3", "2026-08-02T09:00:00+00:00", "2026-08-02T09:30:00+00:00"
            ),
        ]
    }
    service = _FakeService([today, lookahead])
    client = calendar.CalendarClient(make_config(), service=service)

    events, error = client.fetch_events()

    assert error is None
    assert len(events) == 5
    today_scoped = [e for e in events if e.scope == "today"]
    lookahead_scoped = [e for e in events if e.scope == "lookahead"]
    assert [e.id for e in today_scoped] == ["t1", "t2"]
    assert [e.id for e in lookahead_scoped] == ["l1", "l2", "l3"]


def test_fetch_events_credentials_error_returns_error(monkeypatch):
    def _boom(self):
        raise CredentialsError("no refresh token")

    monkeypatch.setattr(calendar.CalendarClient, "_get_service", _boom)
    client = calendar.CalendarClient(make_config())

    events, error = client.fetch_events()

    assert events == []
    assert error is not None
    assert "Calendar fetch failed" in error
    assert "no refresh token" in error


def test_fetch_events_os_error_returns_error():
    service = _FakeService([OSError("network down"), {"items": []}])
    client = calendar.CalendarClient(make_config(), service=service)

    events, error = client.fetch_events()

    assert events == []
    assert error is not None
    assert "Calendar fetch failed" in error


def test_fetch_events_skips_malformed_event():
    good = _timed_event("g", "2026-07-30T09:00:00+00:00", "2026-07-30T09:30:00+00:00")
    bad = {"id": "b", "summary": "broken"}  # no start/end -> ValueError in parser
    service = _FakeService([{"items": [good, bad]}, {"items": []}])
    client = calendar.CalendarClient(make_config(), service=service)

    events, error = client.fetch_events()

    assert error is None
    assert [e.id for e in events] == ["g"]


def test_fetch_events_all_malformed_returns_error():
    bad = {"id": "b", "summary": "broken"}
    service = _FakeService([{"items": [bad]}, {"items": []}])
    client = calendar.CalendarClient(make_config(), service=service)

    events, error = client.fetch_events()

    assert events == []
    assert error is not None
    assert "All 1 fetched events failed to parse" in error


def test_fetch_events_empty_windows_return_empty_no_error():
    service = _FakeService([{"items": []}, {"items": []}])
    client = calendar.CalendarClient(make_config(), service=service)

    events, error = client.fetch_events()

    assert events == []
    assert error is None
