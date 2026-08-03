import base64
import email
from email import policy

from googleapiclient.errors import HttpError

from app.config.loader import GmailConfig
from app.providers import gmail


class _FakeMessages:
    def __init__(
        self, listing=None, messages=None, send_response=None, send_error=None
    ):
        self._listing = listing or {}
        self._messages = messages or {}
        self._send_response = send_response
        self._send_error = send_error
        self.sent_bodies: list[dict] = []

    def list(self, **kwargs):
        return _FakeExec(self._listing)

    def get(self, *, userId, id, format):
        return _FakeExec(self._messages[id])

    def send(self, *, userId, body):
        self.sent_bodies.append(body)
        if self._send_error is not None:
            return _FakeExec(exc=self._send_error)
        return _FakeExec(self._send_response)


class _FakeExec:
    def __init__(self, value=None, exc: Exception | None = None):
        self._value = value
        self._exc = exc

    def execute(self):
        if self._exc is not None:
            raise self._exc
        return self._value


class _FakeUsers:
    def __init__(self, msgs):
        self._msgs = msgs

    def messages(self):
        return self._msgs


class _FakeService:
    def __init__(
        self, listing=None, messages=None, send_response=None, send_error=None
    ):
        self.messages_api = _FakeMessages(
            listing=listing,
            messages=messages,
            send_response=send_response,
            send_error=send_error,
        )
        self._users = _FakeUsers(self.messages_api)

    def users(self):
        return self._users


class _FakeResp:
    def __init__(self, status: int) -> None:
        self.status = status
        self.reason = "server error"


def test_fetch_raw_returns_full_messages():
    listing = {"messages": [{"id": "a"}, {"id": "b"}]}
    messages = {"a": {"id": "a", "payload": {}}, "b": {"id": "b", "payload": {}}}
    service = _FakeService(listing=listing, messages=messages)
    client = gmail.GmailClient(GmailConfig(), service=service)
    result = client.fetch_raw()
    assert [m["id"] for m in result] == ["a", "b"]


def test_fetch_raw_empty_listing():
    service = _FakeService()
    client = gmail.GmailClient(GmailConfig(), service=service)
    assert client.fetch_raw() == []


# ─── send_html ──────────────────────────────────────────────────────────────


def _decode_sent_body(raw_b64: str):
    raw_bytes = base64.urlsafe_b64decode(raw_b64.encode("ascii"))
    return email.message_from_bytes(raw_bytes, policy=policy.default)


def test_send_html_encodes_message_and_returns_id():
    service = _FakeService(send_response={"id": "msg-1"})
    client = gmail.GmailClient(GmailConfig(), service=service)

    message_id, error = client.send_html(
        to="me@example.com", subject="Digest", html_body="<h1>Hi</h1>"
    )

    assert error is None
    assert message_id == "msg-1"
    assert len(service.messages_api.sent_bodies) == 1
    sent = _decode_sent_body(service.messages_api.sent_bodies[0]["raw"])
    assert sent["To"] == "me@example.com"
    assert sent["Subject"] == "Digest"
    html_part = next(
        part for part in sent.walk() if part.get_content_type() == "text/html"
    )
    assert "<h1>Hi</h1>" in html_part.get_content()


def test_send_html_reports_api_error_without_raising():
    err = HttpError(resp=_FakeResp(500), content=b"boom")
    service = _FakeService(send_error=err)
    client = gmail.GmailClient(GmailConfig(), service=service)

    message_id, error = client.send_html(
        to="me@example.com", subject="Digest", html_body="<p/>"
    )

    assert message_id is None
    assert error is not None
    assert "Gmail send failed" in error
