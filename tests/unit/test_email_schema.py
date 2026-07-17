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
