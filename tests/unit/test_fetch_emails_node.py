"""Unit tests for the fetch_emails sensor node (specs.md §3.1.1)."""

from datetime import UTC, datetime

from app.config.loader import (
    Config,
    GmailConfig,
    IdentityConfig,
    LLMConfig,
    PlaneConfig,
    ThresholdsConfig,
)
from app.nodes.fetch_emails import fetch_emails_node
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
        config_hash="x",
    )


def test_fetch_emails_node_writes_emails_and_clears_error(monkeypatch):
    fake_email = RawEmail(
        id="m1",
        thread_id="t1",
        sender="a@b.com",
        subject="s",
        clean_body="hi",
        date=datetime.now(UTC),
    )
    monkeypatch.setattr(gmail, "fetch_emails", lambda cfg: ([fake_email], None))

    node = fetch_emails_node(_config())
    result = node({"errors": {}})

    assert result["emails"] == [fake_email]
    assert result["errors"]["gmail"] is None


def test_fetch_emails_node_writes_error_and_empty_list_on_failure(monkeypatch):
    monkeypatch.setattr(
        gmail, "fetch_emails", lambda cfg: ([], "Gmail fetch failed: boom")
    )

    node = fetch_emails_node(_config())
    result = node({"errors": {}})

    assert result["emails"] == []
    assert result["errors"]["gmail"] == "Gmail fetch failed: boom"


def test_fetch_emails_node_preserves_other_error_keys(monkeypatch):
    monkeypatch.setattr(gmail, "fetch_emails", lambda cfg: ([], None))

    node = fetch_emails_node(_config())
    result = node({"errors": {"calendar": "calendar down"}})

    assert result["errors"] == {"calendar": "calendar down", "gmail": None}
