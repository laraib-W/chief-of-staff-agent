"""Unit tests for the tiered sender filter (specs.md §3.1.1)."""

from datetime import UTC, datetime

from app.config.loader import (
    Config,
    GmailConfig,
    IdentityConfig,
    LLMConfig,
    PlaneConfig,
    ThresholdsConfig,
)
from app.providers import _sender_filter, llm
from app.providers._sender_filter import _domain_of, _extract_address, classify_sender
from app.schemas.email import RawEmail


def test_extract_address_from_name_and_email():
    assert _extract_address("Carol <carol@example.com>") == "carol@example.com"


def test_extract_address_from_bare_email():
    assert _extract_address("carol@example.com") == "carol@example.com"


def test_extract_address_lowercases():
    assert _extract_address("Carol <Carol@Example.COM>") == "carol@example.com"


def test_domain_of():
    assert _domain_of("carol@example.com") == "example.com"


def test_classify_sender_allowlist_match():
    config = GmailConfig(allowlist=["ceo@client.com"])
    assert classify_sender("ceo@client.com", config) == "allowlist"


def test_classify_sender_allowlist_case_insensitive():
    config = GmailConfig(allowlist=["ceo@client.com"])
    assert classify_sender("CEO@Client.com", config) == "allowlist"


def test_classify_sender_trusted_domain_exact_match():
    config = GmailConfig(trusted_domains=["arbisoft.com"])
    assert classify_sender("someone@arbisoft.com", config) == "trusted_domain"


def test_classify_sender_trusted_domain_case_insensitive():
    config = GmailConfig(trusted_domains=["Arbisoft.com"])
    assert classify_sender("someone@ARBISOFT.COM", config) == "trusted_domain"


def test_classify_sender_subdomain_does_not_match_trusted_domain():
    config = GmailConfig(trusted_domains=["arbisoft.com"])
    assert classify_sender("someone@mail.arbisoft.com", config) == "unknown"


def test_classify_sender_unknown_sender():
    config = GmailConfig()
    assert classify_sender("stranger@nowhere.com", config) == "unknown"


def test_classify_sender_allowlist_takes_priority_over_trusted_domain():
    config = GmailConfig(
        allowlist=["ceo@arbisoft.com"], trusted_domains=["notarbisoft.com"]
    )
    assert classify_sender("ceo@arbisoft.com", config) == "allowlist"


def _config(**gmail_overrides) -> Config:
    return Config(
        identity=IdentityConfig(
            user_name="T", timezone="UTC", delivery_address="t@x.com"
        ),
        gmail=GmailConfig(**gmail_overrides),
        plane=PlaneConfig(project_ids=["p1"]),
        thresholds=ThresholdsConfig(),
        llm=LLMConfig(),
        config_hash="x",
    )


def _email(sender: str, **overrides) -> RawEmail:
    fields = dict(
        id="m1",
        thread_id="t1",
        sender=sender,
        subject="Quick question",
        clean_body="Can you help with X?",
        date=datetime.now(UTC),
    )
    fields.update(overrides)
    return RawEmail(**fields)


def test_apply_sender_filter_allowlist_no_llm_call(monkeypatch):
    called = []
    monkeypatch.setattr(llm, "complete", lambda *a, **k: called.append(1) or "yes")

    config = _config(allowlist=["ceo@client.com"])
    emails = [_email("CEO@Client.com")]

    result = _sender_filter.apply_sender_filter(emails, config)

    assert result[0].sender_tier == "allowlist"
    assert called == []


def test_apply_sender_filter_trusted_domain_no_llm_call(monkeypatch):
    called = []
    monkeypatch.setattr(llm, "complete", lambda *a, **k: called.append(1) or "yes")

    config = _config(trusted_domains=["arbisoft.com"])
    emails = [_email("dev@arbisoft.com")]

    result = _sender_filter.apply_sender_filter(emails, config)

    assert result[0].sender_tier == "trusted_domain"
    assert called == []


def test_apply_sender_filter_unknown_sender_llm_trusted(monkeypatch):
    monkeypatch.setattr(llm, "complete", lambda *a, **k: "Yes, this looks real.")

    config = _config()
    emails = [_email("stranger@nowhere.com")]

    result = _sender_filter.apply_sender_filter(emails, config)

    assert result[0].sender_tier == "llm_trusted"


def test_apply_sender_filter_unknown_sender_llm_flagged(monkeypatch):
    monkeypatch.setattr(llm, "complete", lambda *a, **k: "No, this looks automated.")

    config = _config()
    emails = [_email("stranger@nowhere.com")]

    result = _sender_filter.apply_sender_filter(emails, config)

    assert result[0].sender_tier == "llm_flagged"


def test_apply_sender_filter_llm_error_flags_fail_safe(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("api down")

    monkeypatch.setattr(llm, "complete", _boom)

    config = _config()
    emails = [_email("stranger@nowhere.com")]

    result = _sender_filter.apply_sender_filter(emails, config)

    assert result[0].sender_tier == "llm_flagged"


def test_apply_sender_filter_cap_enforced(monkeypatch):
    calls = []
    monkeypatch.setattr(
        llm, "complete", lambda *a, **k: calls.append(1) or "yes"
    )

    config = _config(max_unknown_sender_llm_calls=1)
    emails = [
        _email("first@nowhere.com", id="m1"),
        _email("second@nowhere.com", id="m2"),
    ]

    result = _sender_filter.apply_sender_filter(emails, config)

    assert len(calls) == 1
    tiers = {e.id: e.sender_tier for e in result}
    assert tiers["m1"] == "llm_trusted"
    assert tiers["m2"] == "unchecked"


def test_apply_sender_filter_never_drops_emails(monkeypatch):
    monkeypatch.setattr(llm, "complete", lambda *a, **k: "yes")

    config = _config(max_unknown_sender_llm_calls=0)
    emails = [
        _email("a@nowhere.com", id="m1"),
        _email("b@nowhere.com", id="m2"),
        _email("c@nowhere.com", id="m3"),
    ]

    result = _sender_filter.apply_sender_filter(emails, config)

    assert len(result) == len(emails)
    assert {e.sender_tier for e in result} == {"unchecked"}


def test_apply_sender_filter_preserves_email_order(monkeypatch):
    monkeypatch.setattr(llm, "complete", lambda *a, **k: "yes")

    config = _config(allowlist=["a@x.com"], trusted_domains=["y.com"])
    emails = [
        _email("a@x.com", id="m1"),
        _email("b@y.com", id="m2"),
        _email("c@nowhere.com", id="m3"),
    ]

    result = _sender_filter.apply_sender_filter(emails, config)

    assert [e.id for e in result] == ["m1", "m2", "m3"]
