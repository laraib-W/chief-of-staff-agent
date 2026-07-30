from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.schemas.email import RawEmail


def test_rawemail_valid():
    email = RawEmail(
        id="msg-1",
        thread_id="thr-1",
        sender="Alice <alice@example.com>",
        subject="Hello",
        clean_body="plain text",
        date=datetime(2026, 7, 16, 8, 0, tzinfo=UTC),
    )
    assert email.id == "msg-1"
    assert email.date.tzinfo is not None


def test_rawemail_missing_id_rejected():
    with pytest.raises(ValidationError):
        RawEmail(
            thread_id="thr-1",
            sender="a@b.com",
            subject="s",
            clean_body="b",
            date=datetime.now(UTC),
        )


def _email(**overrides) -> RawEmail:
    fields = dict(
        id="m1",
        thread_id="t1",
        sender="a@b.com",
        subject="s",
        clean_body="hi",
        date=datetime.now(UTC),
    )
    fields.update(overrides)
    return RawEmail(**fields)


def test_sender_tier_defaults_to_unchecked():
    assert _email().sender_tier == "unchecked"


def test_sender_tier_accepts_all_valid_values():
    tiers = ("allowlist", "trusted_domain", "llm_trusted", "llm_flagged", "unchecked")
    for tier in tiers:
        assert _email(sender_tier=tier).sender_tier == tier


def test_sender_tier_rejects_invalid_value():
    with pytest.raises(ValidationError):
        _email(sender_tier="not_a_real_tier")
