"""Integration test: fetch_emails wired into the compiled graph (specs.md §3, §4)."""

from datetime import UTC, datetime

from app.config.loader import (
    Config,
    GmailConfig,
    IdentityConfig,
    LLMConfig,
    PlaneConfig,
    ThresholdsConfig,
)
from app.graph.workflow import build_graph
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


def test_graph_invoke_runs_fetch_emails_node(monkeypatch):
    fake_email = RawEmail(
        id="m1",
        thread_id="t1",
        sender="a@b.com",
        subject="s",
        clean_body="hi",
        date=datetime.now(UTC),
    )
    monkeypatch.setattr(gmail, "fetch_emails", lambda cfg: ([fake_email], None))

    graph = build_graph(_config())
    final_state = graph.invoke({"errors": {}})

    assert final_state["emails"] == [fake_email]
    assert final_state["errors"]["gmail"] is None
