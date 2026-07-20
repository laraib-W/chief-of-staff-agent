"""Integration test: fetch_emails wired into the compiled graph (specs.md §3, §4)."""

from datetime import UTC, datetime

from app.graph.workflow import build_graph
from app.providers import gmail
from app.schemas.email import RawEmail
from tests.conftest import make_config


def test_graph_invoke_runs_fetch_emails_node(monkeypatch):
    fake_email = RawEmail(
        id="m1",
        thread_id="t1",
        sender="a@b.com",
        subject="s",
        clean_body="hi",
        date=datetime.now(UTC),
    )
    monkeypatch.setattr(
        gmail.GmailClient, "fetch_emails", lambda self: ([fake_email], None)
    )

    graph = build_graph(make_config())
    final_state = graph.invoke({"errors": {}})

    assert final_state["emails"] == [fake_email]
    assert final_state["errors"]["gmail"] is None
