import base64
from datetime import UTC

from app.providers import _sanitize


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


def _raw(body_text="hi", mime="text/plain", parts=None):
    payload = {"mimeType": mime, "headers": [
        {"name": "From", "value": "Alice <alice@example.com>"},
        {"name": "Subject", "value": "Hello"},
    ]}
    if parts is not None:
        payload["parts"] = parts
    else:
        payload["body"] = {"data": _b64(body_text)}
    return {
        "id": "m1",
        "threadId": "t1",
        "internalDate": "1752652800000",
        "payload": payload,
    }


def test_extract_headers():
    h = _sanitize.extract_headers(_raw())
    assert h["id"] == "m1"
    assert h["thread_id"] == "t1"
    assert h["sender"] == "Alice <alice@example.com>"
    assert h["subject"] == "Hello"
    assert h["date"].tzinfo == UTC


def test_extract_headers_missing_defaults_empty():
    raw = _raw()
    raw["payload"]["headers"] = []
    h = _sanitize.extract_headers(raw)
    assert h["sender"] == ""
    assert h["subject"] == ""


def test_extract_body_single_plain_part():
    assert _sanitize.extract_body(_raw(body_text="hello world")) == "hello world"


def test_extract_body_prefers_plain_over_html():
    parts = [
        {"mimeType": "text/html", "body": {"data": _b64("<p>html</p>")}},
        {"mimeType": "text/plain", "body": {"data": _b64("plain")}},
    ]
    raw = _raw(mime="multipart/alternative", parts=parts)
    assert _sanitize.extract_body(raw) == "plain"


def test_extract_body_falls_back_to_html():
    parts = [{"mimeType": "text/html", "body": {"data": _b64("<p>html</p>")}}]
    raw = _raw(mime="multipart/alternative", parts=parts)
    assert _sanitize.extract_body(raw) == "<p>html</p>"
