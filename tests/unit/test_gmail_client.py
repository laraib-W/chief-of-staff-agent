import pytest

from app.config.loader import GmailConfig
from app.providers import gmail


class _FakeMessages:
    def __init__(self, listing, messages):
        self._listing = listing
        self._messages = messages

    def list(self, **kwargs):
        return _FakeExec(self._listing)

    def get(self, *, userId, id, format):
        return _FakeExec(self._messages[id])


class _FakeExec:
    def __init__(self, value):
        self._value = value

    def execute(self):
        return self._value


class _FakeUsers:
    def __init__(self, msgs):
        self._msgs = msgs

    def messages(self):
        return self._msgs


class _FakeService:
    def __init__(self, listing, messages):
        self._users = _FakeUsers(_FakeMessages(listing, messages))

    def users(self):
        return self._users


def test_fetch_raw_returns_full_messages():
    listing = {"messages": [{"id": "a"}, {"id": "b"}]}
    messages = {"a": {"id": "a", "payload": {}}, "b": {"id": "b", "payload": {}}}
    service = _FakeService(listing, messages)
    client = gmail.GmailClient(GmailConfig(), service=service)
    result = client.fetch_raw()
    assert [m["id"] for m in result] == ["a", "b"]


def test_fetch_raw_empty_listing():
    service = _FakeService({}, {})
    client = gmail.GmailClient(GmailConfig(), service=service)
    assert client.fetch_raw() == []


def test_load_credentials_missing_token_raises(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "secret")
    monkeypatch.setattr(gmail.keyring, "get_password", lambda service, user: None)
    with pytest.raises(gmail.GmailAuthError, match="app.auth"):
        gmail._load_credentials()
