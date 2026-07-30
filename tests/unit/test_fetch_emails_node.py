"""Unit tests for the fetch_emails sensor node (specs.md §3.1.1)."""

from datetime import UTC, datetime

from app.nodes.fetch_emails import fetch_emails_node
from app.providers import gmail
from app.schemas.email import RawEmail
from tests.conftest import make_config


def test_fetch_emails_node_writes_emails_and_clears_error(monkeypatch):
    fake_email = RawEmail(
        id="m1",
        thread_id="t1",
        sender="a@b.com",
        subject="s",
        clean_body="hi",
        date=datetime.now(UTC),
    )
    monkeypatch.setattr(
        gmail.GmailClient, "fetch_emails", lambda self, llm_config: ([fake_email], None)
    )

    node = fetch_emails_node(make_config())
    result = node({"errors": {}})

    assert result["emails"] == [fake_email]
    assert result["errors"]["gmail"] is None


def test_fetch_emails_node_writes_error_and_empty_list_on_failure(monkeypatch):
    monkeypatch.setattr(
        gmail.GmailClient,
        "fetch_emails",
        lambda self, llm_config: ([], "Gmail fetch failed: boom"),
    )

    node = fetch_emails_node(make_config())
    result = node({"errors": {}})

    assert result["emails"] == []
    assert result["errors"]["gmail"] == "Gmail fetch failed: boom"


def test_fetch_emails_node_preserves_other_error_keys(monkeypatch):
    monkeypatch.setattr(
        gmail.GmailClient, "fetch_emails", lambda self, llm_config: ([], None)
    )

    node = fetch_emails_node(make_config())
    result = node({"errors": {"calendar": "calendar down"}})

    assert result["errors"] == {"calendar": "calendar down", "gmail": None}
