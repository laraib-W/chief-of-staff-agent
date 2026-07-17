import base64

from app.providers import _sanitize
from app.schemas.email import RawEmail


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


def test_normalize_unicode_strips_zero_width():
    assert _sanitize.normalize_unicode("a​b﻿c") == "abc"


def test_normalize_unicode_strips_bidi_override():
    assert _sanitize.normalize_unicode("x‮y") == "xy"


def test_truncate_caps_length():
    assert _sanitize.truncate("abcdef", 3) == "abc"


def test_truncate_under_cap_passthrough():
    assert _sanitize.truncate("ab", 10) == "ab"


def test_sanitize_message_full_pipeline():
    body = "<p>Real ask here.</p>\n> quoted old\n-- \nJane"
    raw = {
        "id": "m9", "threadId": "t9", "internalDate": "1752652800000",
        "payload": {
            "mimeType": "text/html",
            "headers": [
                {"name": "From", "value": "Bob <bob@x.com>"},
                {"name": "Subject", "value": "Ask"},
            ],
            "body": {"data": _b64(body)},
        },
    }
    email = _sanitize.sanitize_message(raw, max_body_chars=2000)
    assert isinstance(email, RawEmail)
    assert email.id == "m9"
    assert email.sender == "Bob <bob@x.com>"
    assert "Real ask here." in email.clean_body
    assert "quoted old" not in email.clean_body
    assert "Jane" not in email.clean_body


def test_sanitize_message_respects_max_chars():
    raw = {
        "id": "m10", "threadId": "t10", "internalDate": "1752652800000",
        "payload": {
            "mimeType": "text/plain",
            "headers": [{"name": "Subject", "value": "s"}],
            "body": {"data": _b64("x" * 100)},
        },
    }
    email = _sanitize.sanitize_message(raw, max_body_chars=10)
    assert len(email.clean_body) == 10


def test_sanitize_message_html_only_no_literal_newlines_strips_quote_and_signature():
    """Regression: HTML-only body with NO literal '\\n' chars must still have
    quote chains and signatures stripped, because strip_html must emit
    newlines at block boundaries for downstream strip_quotes/strip_signature
    (which rely on re.MULTILINE / splitlines()) to work.
    """
    body = (
        "<div>Real ask here.</div>"
        "<div>On Mon, Jul 14, 2026 Bob wrote:</div>"
        "<blockquote>old secret</blockquote>"
        "<div>-- </div>"
        "<div>Jane Doe</div>"
    )
    raw = {
        "id": "m11", "threadId": "t11", "internalDate": "1752652800000",
        "payload": {
            "mimeType": "text/html",
            "headers": [{"name": "Subject", "value": "Ask"}],
            "body": {"data": _b64(body)},
        },
    }
    email = _sanitize.sanitize_message(raw, max_body_chars=2000)
    assert email.clean_body == "Real ask here."
