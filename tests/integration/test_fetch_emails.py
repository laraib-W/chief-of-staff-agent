import pytest

from app.config.loader import (
    Config,
    GmailConfig,
    IdentityConfig,
    LLMConfig,
    PlaneConfig,
    ThresholdsConfig,
)
from app.providers import gmail
from app.schemas.email import RawEmail


def _config() -> Config:
    return Config(
        identity=IdentityConfig(
            user_name="T", timezone="UTC", delivery_address="t@x.com"
        ),
        gmail=GmailConfig(),
        plane=PlaneConfig(project_ids=["p1"]),
        thresholds=ThresholdsConfig(),
        llm=LLMConfig(),
        config_hash="deadbeef",
    )


@pytest.mark.replay
def test_fetch_emails_replay_sanitizes_fixture():
    emails, error = gmail.fetch_emails(_config(), replay=True)
    assert error is None
    assert len(emails) == 1
    assert isinstance(emails[0], RawEmail)
    assert emails[0].id == "fix-1"
    assert emails[0].clean_body == "Hello team"


def test_fetch_emails_error_returns_empty(monkeypatch):
    def _boom(self):
        raise RuntimeError("gmail down")

    monkeypatch.setattr(gmail.GmailClient, "fetch_raw", _boom)
    emails, error = gmail.fetch_emails(_config())
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
    emails, error = gmail.fetch_emails(_config())
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

    monkeypatch.setattr(
        gmail.GmailClient, "fetch_raw", lambda self: [good, non_dict]
    )
    emails, error = gmail.fetch_emails(_config())
    assert error is None
    assert [e.id for e in emails] == ["g2"]
