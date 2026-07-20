import pytest

from app.auth import CredentialsError
from app.providers import gmail
from app.schemas.email import RawEmail
from tests.conftest import load_gmail_fixture, make_config


@pytest.mark.replay
def test_fetch_emails_replay_sanitizes_fixture(monkeypatch):
    fixture = load_gmail_fixture()
    monkeypatch.setattr(gmail.GmailClient, "fetch_raw", lambda self: fixture)

    emails, error = gmail.GmailClient(make_config().gmail).fetch_emails()
    assert error is None
    assert len(emails) == 1
    assert isinstance(emails[0], RawEmail)
    assert emails[0].id == "fix-1"
    assert emails[0].clean_body == "Hello team"


def test_fetch_emails_error_returns_empty(monkeypatch):
    def _boom(self):
        raise CredentialsError("gmail down")

    monkeypatch.setattr(gmail.GmailClient, "fetch_raw", _boom)
    emails, error = gmail.GmailClient(make_config().gmail).fetch_emails()
    assert emails == []
    assert "gmail down" in error


def test_fetch_emails_skips_malformed_message(monkeypatch):
    good = {
        "id": "g",
        "threadId": "tg",
        "internalDate": "1752652800000",
        "payload": {"mimeType": "text/plain", "headers": [], "body": {"data": "aGk="}},
    }
    bad = {
        "id": "b",
        "internalDate": "not-a-number",
        "payload": {"headers": []},
    }  # int() raises -> skipped

    monkeypatch.setattr(gmail.GmailClient, "fetch_raw", lambda self: [good, bad])
    emails, error = gmail.GmailClient(make_config().gmail).fetch_emails()
    assert error is None
    assert [e.id for e in emails] == ["g"]


def test_fetch_emails_skip_handler_survives_non_dict_message(monkeypatch):
    good = {
        "id": "g2",
        "threadId": "tg2",
        "internalDate": "1752652800000",
        "payload": {"mimeType": "text/plain", "headers": [], "body": {"data": "aGk="}},
    }
    non_dict = "garbage"

    monkeypatch.setattr(gmail.GmailClient, "fetch_raw", lambda self: [good, non_dict])
    emails, error = gmail.GmailClient(make_config().gmail).fetch_emails()
    assert error is None
    assert [e.id for e in emails] == ["g2"]


def test_fetch_emails_all_messages_failed_returns_error(monkeypatch):
    bad = {
        "id": "b",
        "internalDate": "not-a-number",
        "payload": {"headers": []},
    }  # int() raises -> skipped, and it's the only message

    monkeypatch.setattr(gmail.GmailClient, "fetch_raw", lambda self: [bad])
    emails, error = gmail.GmailClient(make_config().gmail).fetch_emails()
    assert emails == []
    assert error is not None
    assert "All 1 fetched messages failed to sanitize" in error
