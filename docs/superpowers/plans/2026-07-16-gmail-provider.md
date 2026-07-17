# Gmail Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the read-only Gmail provider — authenticate, fetch the last N hours of mail, and run every message through a sanitization pipeline into clean `RawEmail` objects.

**Architecture:** Two layers. `GmailClient` (in `app/providers/gmail.py`) owns credential loading and the raw Gmail API call; `app/providers/_sanitize.py` holds pure, I/O-free sanitization functions. A thin `fetch_emails` orchestrator maps raw dicts through sanitize and returns `(emails, error)`. `--replay` swaps exactly one method — `GmailClient.fetch_raw` — for a fixture reader.

**Tech Stack:** Python ≥3.11, Pydantic v2, `google-auth` / `google-auth-oauthlib` / `google-api-python-client`, `keyring`, `beautifulsoup4` + `lxml`, `structlog`, pytest.

## Global Constraints

- **Read-only.** Provider only issues Gmail read calls. Never replies, labels, or writes.
- **No credentials or email bodies in LangGraph state or logs** (SECURITY.md §2.2, §11). Credentials come from env + keyring; the provider returns plain values and never touches `AgentState`. Logs carry counts/ids/timings only.
- **Python `requires-python = ">=3.11"`.**
- **Sanitized output only.** No raw HTML, tracking metadata, or attachment content may appear in `RawEmail.clean_body` (SECURITY.md §3).
- **Body length cap default: 2000 chars** (`gmail.max_body_chars`).
- **BeautifulSoup is a deliberate exception** to SECURITY.md §4 dependency-minimization (email HTML is too messy for stdlib). Pin it; no other new runtime deps.
- **Ruff lint is enforced** (`E,F,W,I,UP,S,PT`). Keep imports sorted; no bare excepts flagged by `S`.
- **Keyring identity:** service `"cos-agent"`, username `"google_refresh_token"`.
- **OAuth scope:** `https://www.googleapis.com/auth/gmail.readonly`.

---

### Task 1: Dependencies, config key, and `RawEmail` schema

**Files:**
- Modify: `pyproject.toml` (add `beautifulsoup4`, `lxml` to `dependencies`)
- Modify: `app/config/loader.py:28-31` (`GmailConfig` — add `max_body_chars`)
- Modify: `config.example.yaml` (document the new key)
- Modify: `app/schemas/email.py` (flesh out `RawEmail`)
- Test: `tests/unit/test_email_schema.py`

**Interfaces:**
- Produces: `RawEmail(id: str, thread_id: str, sender: str, subject: str, clean_body: str, date: datetime)` — Pydantic v2 `BaseModel`.
- Produces: `GmailConfig.max_body_chars: int = 2000`.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_email_schema.py`:

```python
from datetime import datetime, timezone

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
        date=datetime(2026, 7, 16, 8, 0, tzinfo=timezone.utc),
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
            date=datetime.now(timezone.utc),
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_email_schema.py -v`
Expected: FAIL — `RawEmail` has no such fields / `TypeError`.

- [ ] **Step 3: Implement the schema**

Replace `app/schemas/email.py` `RawEmail` with:

```python
"""RawEmail and EmailAction schemas (specs.md §3.1.1, §3.2.1)."""

from datetime import datetime

from pydantic import BaseModel


class RawEmail(BaseModel):
    """Sanitized email as it enters agent state. Fields land in fetch_emails."""

    id: str
    thread_id: str
    sender: str
    subject: str
    clean_body: str
    date: datetime


class EmailAction(BaseModel):
    """LLM classification output. Fields land in classify_emails."""
```

- [ ] **Step 4: Add the config key**

In `app/config/loader.py`, extend `GmailConfig`:

```python
class GmailConfig(_Strict):
    allowlist: list[str] = Field(default_factory=list)
    trusted_domains: list[str] = Field(default_factory=list)
    fetch_window_hours: int = 24
    max_body_chars: int = 2000
```

In `config.example.yaml`, under the `gmail:` block after `fetch_window_hours: 24`:

```yaml
  max_body_chars: 2000                   # body length cap (SECURITY.md §3)
```

- [ ] **Step 5: Add dependencies**

In `pyproject.toml` `dependencies`, add:

```toml
    "beautifulsoup4>=4.12.0",
    "lxml>=5.0.0",
```

Run: `uv sync`
Expected: lockfile resolves, both packages install.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_email_schema.py -v`
Expected: PASS (2 passed).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock app/config/loader.py config.example.yaml app/schemas/email.py tests/unit/test_email_schema.py
git commit -m "feat(gmail): RawEmail schema, max_body_chars config, BS4 deps"
```

---

### Task 2: Header and body extraction

**Files:**
- Create: `app/providers/_sanitize.py`
- Test: `tests/unit/test_sanitize_extract.py`

**Interfaces:**
- Produces: `extract_headers(raw: dict) -> dict` — returns keys `id`, `thread_id`, `sender`, `subject`, `date` (an aware UTC `datetime`). Missing `From`/`Subject` headers default to `""`.
- Produces: `extract_body(raw: dict) -> str` — decoded body text; prefers a `text/plain` part, else `text/html`, else `""`. Handles both `payload.body.data` (single part) and nested `payload.parts`.
- Consumes: Gmail `messages.get(format="full")` dict shape: `id`, `threadId`, `internalDate` (ms-epoch string), `payload.headers` (list of `{name, value}`), `payload.mimeType`, `payload.body.data` (base64url), `payload.parts` (recursive).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_sanitize_extract.py`:

```python
import base64
from datetime import timezone

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
    return {"id": "m1", "threadId": "t1", "internalDate": "1752652800000", "payload": payload}


def test_extract_headers():
    h = _sanitize.extract_headers(_raw())
    assert h["id"] == "m1"
    assert h["thread_id"] == "t1"
    assert h["sender"] == "Alice <alice@example.com>"
    assert h["subject"] == "Hello"
    assert h["date"].tzinfo == timezone.utc


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
    assert _sanitize.extract_body(_raw(mime="multipart/alternative", parts=parts)) == "plain"


def test_extract_body_falls_back_to_html():
    parts = [{"mimeType": "text/html", "body": {"data": _b64("<p>html</p>")}}]
    assert _sanitize.extract_body(_raw(mime="multipart/alternative", parts=parts)) == "<p>html</p>"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_sanitize_extract.py -v`
Expected: FAIL — `ModuleNotFoundError: app.providers._sanitize`.

- [ ] **Step 3: Implement extraction**

Create `app/providers/_sanitize.py`:

```python
"""Pure email sanitization functions — no I/O (SECURITY.md §3)."""

from __future__ import annotations

import base64
from datetime import datetime, timezone


def _b64url_decode(data: str) -> str:
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode()).decode("utf-8", errors="replace")


def _header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def extract_headers(raw: dict) -> dict:
    """Return id, thread_id, sender, subject, and an aware UTC date."""
    headers = raw.get("payload", {}).get("headers", [])
    internal_ms = int(raw.get("internalDate", "0"))
    return {
        "id": raw.get("id", ""),
        "thread_id": raw.get("threadId", ""),
        "sender": _header(headers, "From"),
        "subject": _header(headers, "Subject"),
        "date": datetime.fromtimestamp(internal_ms / 1000, tz=timezone.utc),
    }


def _walk_parts(payload: dict, mime: str) -> str | None:
    if payload.get("mimeType") == mime:
        data = payload.get("body", {}).get("data")
        if data:
            return _b64url_decode(data)
    for part in payload.get("parts", []):
        found = _walk_parts(part, mime)
        if found is not None:
            return found
    return None


def extract_body(raw: dict) -> str:
    """Best available body: prefer text/plain, else text/html, else ''."""
    payload = raw.get("payload", {})
    for mime in ("text/plain", "text/html"):
        body = _walk_parts(payload, mime)
        if body is not None:
            return body
    return ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_sanitize_extract.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add app/providers/_sanitize.py tests/unit/test_sanitize_extract.py
git commit -m "feat(gmail): header and body extraction"
```

---

### Task 3: HTML and tracking-link stripping

**Files:**
- Modify: `app/providers/_sanitize.py`
- Test: `tests/unit/test_sanitize_html.py`

**Interfaces:**
- Produces: `strip_html(text: str) -> str` — BeautifulSoup `get_text`, drops `<script>`/`<style>`, collapses whitespace.
- Produces: `strip_tracking(text: str) -> str` — removes URLs on known tracking domains entirely and strips `utm_*` query params from surviving URLs.
- Produces: `TRACKING_DOMAINS: frozenset[str]` constant.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_sanitize_html.py`:

```python
from app.providers import _sanitize


def test_strip_html_removes_tags():
    assert _sanitize.strip_html("<p>Hello <b>world</b></p>") == "Hello world"


def test_strip_html_drops_script_and_style():
    html = "<style>.x{}</style><p>keep</p><script>evil()</script>"
    assert _sanitize.strip_html(html) == "keep"


def test_strip_html_plain_text_passthrough():
    assert _sanitize.strip_html("just text") == "just text"


def test_strip_tracking_removes_tracking_url():
    text = "See http://click.mailchimp.com/abc now"
    assert "click.mailchimp.com" not in _sanitize.strip_tracking(text)


def test_strip_tracking_strips_utm_params():
    text = "Visit https://example.com/page?utm_source=x&id=5&utm_medium=y"
    out = _sanitize.strip_tracking(text)
    assert "utm_source" not in out
    assert "id=5" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_sanitize_html.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'strip_html'`.

- [ ] **Step 3: Implement HTML and tracking strip**

Append to `app/providers/_sanitize.py` (add `import re` and the BeautifulSoup import at top):

```python
import re
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

from bs4 import BeautifulSoup

TRACKING_DOMAINS: frozenset[str] = frozenset({
    "click.mailchimp.com",
    "list-manage.com",
    "sendgrid.net",
    "mandrillapp.com",
    "sparkpostmail.com",
    "click.e.example.com",
})

_URL_RE = re.compile(r"https?://[^\s)>\]]+")
_WS_RE = re.compile(r"[ \t]+")


def strip_html(text: str) -> str:
    soup = BeautifulSoup(text, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()
    plain = soup.get_text(separator=" ")
    plain = _WS_RE.sub(" ", plain)
    return "\n".join(line.strip() for line in plain.splitlines()).strip()


def _clean_url(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if any(host == d or host.endswith("." + d) for d in TRACKING_DOMAINS):
        return None
    kept = [(k, v) for k, v in parse_qsl(parsed.query) if not k.lower().startswith("utm_")]
    return urlunparse(parsed._replace(query=urlencode(kept)))


def strip_tracking(text: str) -> str:
    def _sub(m: re.Match) -> str:
        cleaned = _clean_url(m.group(0))
        return "" if cleaned is None else cleaned

    return _URL_RE.sub(_sub, text)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_sanitize_html.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add app/providers/_sanitize.py tests/unit/test_sanitize_html.py
git commit -m "feat(gmail): HTML and tracking-link stripping"
```

---

### Task 4: Quoted-reply and signature stripping

**Files:**
- Modify: `app/providers/_sanitize.py`
- Test: `tests/unit/test_sanitize_quotes.py`

**Interfaces:**
- Produces: `strip_quotes(text: str) -> str` — cuts from the first quoted-reply marker: a line starting with `>`, an `On … wrote:` block, or an Outlook `-----Original Message-----` separator.
- Produces: `strip_signature(text: str) -> str` — cuts the signature tail from a `-- ` delimiter line, a `Sent from my …` line, or a `Regards,`/`Best,`/`Thanks,` closing followed by a short name line.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_sanitize_quotes.py`:

```python
from app.providers import _sanitize


def test_strip_quotes_gt_lines():
    text = "My reply.\n> previous line\n> more quoted"
    assert _sanitize.strip_quotes(text) == "My reply."


def test_strip_quotes_on_wrote_block():
    text = "Answer here.\nOn Mon, Jul 14, 2026 at 9:00 AM Bob <b@x.com> wrote:\nold stuff"
    assert _sanitize.strip_quotes(text) == "Answer here."


def test_strip_quotes_outlook_separator():
    text = "Reply body.\n-----Original Message-----\nFrom: Bob"
    assert _sanitize.strip_quotes(text) == "Reply body."


def test_strip_quotes_no_marker_passthrough():
    assert _sanitize.strip_quotes("clean body") == "clean body"


def test_strip_signature_dashdash_delimiter():
    text = "Body text.\n-- \nJane Doe\nCEO"
    assert _sanitize.strip_signature(text) == "Body text."


def test_strip_signature_sent_from_my():
    text = "Quick note.\nSent from my iPhone"
    assert _sanitize.strip_signature(text) == "Quick note."


def test_strip_signature_regards_closing():
    text = "Please review.\nRegards,\nJane"
    assert _sanitize.strip_signature(text) == "Please review."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_sanitize_quotes.py -v`
Expected: FAIL — attributes `strip_quotes` / `strip_signature` missing.

- [ ] **Step 3: Implement quote and signature strip**

Append to `app/providers/_sanitize.py`:

```python
_ON_WROTE_RE = re.compile(r"^On .+wrote:$", re.MULTILINE)
_OUTLOOK_RE = re.compile(r"^-{2,}\s*Original Message\s*-{2,}$", re.MULTILINE | re.IGNORECASE)
_SIG_CLOSING_RE = re.compile(r"^(regards|best|thanks|cheers|sincerely)[,!.]?$", re.IGNORECASE)


def _earliest_cut(text: str, indices: list[int]) -> str:
    real = [i for i in indices if i >= 0]
    if not real:
        return text.strip()
    return text[: min(real)].strip()


def strip_quotes(text: str) -> str:
    cuts: list[int] = []

    gt = re.search(r"^>", text, re.MULTILINE)
    cuts.append(gt.start() if gt else -1)

    on_wrote = _ON_WROTE_RE.search(text)
    cuts.append(on_wrote.start() if on_wrote else -1)

    outlook = _OUTLOOK_RE.search(text)
    cuts.append(outlook.start() if outlook else -1)

    return _earliest_cut(text, cuts)


def strip_signature(text: str) -> str:
    lines = text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.rstrip()
        if stripped == "--" or line == "-- ":
            return "\n".join(lines[:i]).strip()
        if stripped.lower().startswith("sent from my "):
            return "\n".join(lines[:i]).strip()
        if _SIG_CLOSING_RE.match(stripped):
            return "\n".join(lines[:i]).strip()
    return text.strip()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_sanitize_quotes.py -v`
Expected: PASS (7 passed).

- [ ] **Step 5: Commit**

```bash
git add app/providers/_sanitize.py tests/unit/test_sanitize_quotes.py
git commit -m "feat(gmail): quoted-reply and signature stripping"
```

---

### Task 5: Unicode normalization, truncation, and `sanitize_message`

**Files:**
- Modify: `app/providers/_sanitize.py`
- Test: `tests/unit/test_sanitize_message.py`

**Interfaces:**
- Produces: `normalize_unicode(text: str) -> str` — NFC; strips zero-width (U+200B–200D, U+FEFF) and bidi overrides (U+202A–202E, U+2066–2069).
- Produces: `truncate(text: str, max_chars: int) -> str`.
- Produces: `sanitize_message(raw: dict, max_body_chars: int) -> RawEmail` — extracts headers/body then applies, in order: `strip_html` → `strip_tracking` → `strip_quotes` → `strip_signature` → `normalize_unicode` → `truncate`.
- Consumes: `RawEmail` (Task 1); all extraction/strip functions (Tasks 2–4).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_sanitize_message.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_sanitize_message.py -v`
Expected: FAIL — `normalize_unicode` / `sanitize_message` missing.

- [ ] **Step 3: Implement normalization, truncation, and compose**

Add `import unicodedata` at the top of `app/providers/_sanitize.py`, add the `RawEmail` import (`from app.schemas.email import RawEmail`), then append:

```python
_INVISIBLE = dict.fromkeys(
    [0x200B, 0x200C, 0x200D, 0xFEFF, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E,
     0x2066, 0x2067, 0x2068, 0x2069],
    None,
)


def normalize_unicode(text: str) -> str:
    return unicodedata.normalize("NFC", text).translate(_INVISIBLE)


def truncate(text: str, max_chars: int) -> str:
    return text[:max_chars]


def sanitize_message(raw: dict, max_body_chars: int) -> RawEmail:
    headers = extract_headers(raw)
    body = extract_body(raw)
    body = strip_html(body)
    body = strip_tracking(body)
    body = strip_quotes(body)
    body = strip_signature(body)
    body = normalize_unicode(body)
    body = truncate(body, max_body_chars)
    return RawEmail(
        id=headers["id"],
        thread_id=headers["thread_id"],
        sender=headers["sender"],
        subject=headers["subject"],
        clean_body=body,
        date=headers["date"],
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_sanitize_message.py -v`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add app/providers/_sanitize.py tests/unit/test_sanitize_message.py
git commit -m "feat(gmail): unicode normalize, truncate, sanitize_message compose"
```

---

### Task 6: `GmailClient` — credential loader and `fetch_raw`

**Files:**
- Modify: `app/providers/gmail.py`
- Test: `tests/unit/test_gmail_client.py`

**Interfaces:**
- Produces: `GmailAuthError(Exception)`.
- Produces: `GmailClient(gmail_config: GmailConfig, service=None)`; `.fetch_raw() -> list[dict]`. When `service` is `None`, `fetch_raw` lazily builds the authenticated service from env + keyring; tests pass a fake `service` to bypass network.
- Produces: `_load_credentials() -> Credentials` — reads client id/secret from env, refresh token from keyring; raises `GmailAuthError` (message references `python -m app.auth`) when the refresh token is absent.
- Consumes: `GmailConfig` (Task 1: `fetch_window_hours`).

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_gmail_client.py`:

```python
import pytest

from app.config.loader import GmailConfig
from app.providers import gmail


class _FakeMessages:
    def __init__(self, listing, messages):
        self._listing = listing
        self._messages = messages

    def list(self, **kwargs):
        return _FakeExec(self._listing)

    def get(self, *, userId, id, format):
        return _FakeExec(self._messages[id])


class _FakeExec:
    def __init__(self, value):
        self._value = value

    def execute(self):
        return self._value


class _FakeUsers:
    def __init__(self, msgs):
        self._msgs = msgs

    def messages(self):
        return self._msgs


class _FakeService:
    def __init__(self, listing, messages):
        self._users = _FakeUsers(_FakeMessages(listing, messages))

    def users(self):
        return self._users


def test_fetch_raw_returns_full_messages():
    listing = {"messages": [{"id": "a"}, {"id": "b"}]}
    messages = {"a": {"id": "a", "payload": {}}, "b": {"id": "b", "payload": {}}}
    service = _FakeService(listing, messages)
    client = gmail.GmailClient(GmailConfig(), service=service)
    result = client.fetch_raw()
    assert [m["id"] for m in result] == ["a", "b"]


def test_fetch_raw_empty_listing():
    service = _FakeService({}, {})
    client = gmail.GmailClient(GmailConfig(), service=service)
    assert client.fetch_raw() == []


def test_load_credentials_missing_token_raises(monkeypatch):
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "secret")
    monkeypatch.setattr(gmail.keyring, "get_password", lambda service, user: None)
    with pytest.raises(gmail.GmailAuthError, match="app.auth"):
        gmail._load_credentials()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_gmail_client.py -v`
Expected: FAIL — `GmailClient` / `GmailAuthError` / `_load_credentials` missing.

- [ ] **Step 3: Implement the client**

Replace `app/providers/gmail.py` with:

```python
"""Gmail provider — read-only, includes the sanitization pipeline (SECURITY.md §3)."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import keyring
import structlog
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from app.config.loader import GmailConfig

log = structlog.get_logger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
KEYRING_SERVICE = "cos-agent"
KEYRING_USERNAME = "google_refresh_token"
TOKEN_URI = "https://oauth2.googleapis.com/token"


class GmailAuthError(Exception):
    """Raised when Gmail credentials cannot be loaded."""


def _load_credentials() -> Credentials:
    refresh_token = keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
    if not refresh_token:
        raise GmailAuthError(
            "No Gmail refresh token in keyring. Run `python -m app.auth` to authenticate."
        )
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        client_id=os.environ["GOOGLE_OAUTH_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
        token_uri=TOKEN_URI,
        scopes=SCOPES,
    )
    creds.refresh(Request())
    return creds


class GmailClient:
    """Read-only Gmail API client. Pass ``service`` to bypass live auth in tests."""

    def __init__(self, gmail_config: GmailConfig, service=None) -> None:
        self._config = gmail_config
        self._service = service

    def _get_service(self):
        if self._service is None:
            self._service = build("gmail", "v1", credentials=_load_credentials())
        return self._service

    def fetch_raw(self) -> list[dict]:
        """Return full raw message dicts received within the fetch window."""
        service = self._get_service()
        cutoff = datetime.now(timezone.utc) - timedelta(
            hours=self._config.fetch_window_hours
        )
        query = f"after:{int(cutoff.timestamp())}"

        messages_api = service.users().messages()
        listing = messages_api.list(userId="me", q=query).execute()
        ids = [m["id"] for m in listing.get("messages", [])]
        log.info("gmail.fetch_raw", count=len(ids))

        return [
            messages_api.get(userId="me", id=mid, format="full").execute()
            for mid in ids
        ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_gmail_client.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add app/providers/gmail.py tests/unit/test_gmail_client.py
git commit -m "feat(gmail): GmailClient credential loader and fetch_raw"
```

---

### Task 7: Fixture helpers and `fetch_emails` orchestrator

**Files:**
- Modify: `app/providers/gmail.py`
- Create: `tests/fixtures/gmail_2026-07-16.json`
- Test: `tests/integration/test_fetch_emails.py`

**Interfaces:**
- Produces: `FIXTURE_DIR: Path` — `tests/fixtures/`.
- Produces: `load_raw_from_fixture(date: str) -> list[dict]`; `record_raw(raw: list[dict], date: str) -> None`.
- Produces: `fetch_emails(config: Config, *, replay: bool = False, record: bool = False) -> tuple[list[RawEmail], str | None]`. On success returns `(emails, None)`; on any fetch failure returns `([], "<error>")`; per-message sanitize failures are logged and skipped.
- Consumes: `GmailClient` (Task 6), `sanitize_message` (Task 5), `Config`/`GmailConfig` (Task 1).

- [ ] **Step 1: Write the failing test**

Create `tests/fixtures/gmail_2026-07-16.json` (base64 body `"PGgxPkhlbGxvIHRlYW08L2gxPg=="` decodes to `<h1>Hello team</h1>`):

```json
[
  {
    "id": "fix-1",
    "threadId": "fix-t1",
    "internalDate": "1752652800000",
    "payload": {
      "mimeType": "text/html",
      "headers": [
        {"name": "From", "value": "Carol <carol@example.com>"},
        {"name": "Subject", "value": "Fixture subject"}
      ],
      "body": {"data": "PGgxPkhlbGxvIHRlYW08L2gxPg=="}
    }
  }
]
```

Create `tests/integration/test_fetch_emails.py`:

```python
import pytest

from app.config.loader import Config, GmailConfig, IdentityConfig, LLMConfig, PlaneConfig, ThresholdsConfig
from app.providers import gmail
from app.schemas.email import RawEmail


def _config() -> Config:
    return Config(
        identity=IdentityConfig(user_name="T", timezone="UTC", delivery_address="t@x.com"),
        gmail=GmailConfig(),
        plane=PlaneConfig(project_ids=["p1"]),
        thresholds=ThresholdsConfig(),
        llm=LLMConfig(),
        config_hash="deadbeef",
    )


@pytest.mark.replay
def test_fetch_emails_replay_sanitizes_fixture():
    emails, error = gmail.fetch_emails(_config(), replay=True)
    assert error is None
    assert len(emails) == 1
    assert isinstance(emails[0], RawEmail)
    assert emails[0].id == "fix-1"
    assert emails[0].clean_body == "Hello team"


def test_fetch_emails_error_returns_empty(monkeypatch):
    def _boom(self):
        raise RuntimeError("gmail down")

    monkeypatch.setattr(gmail.GmailClient, "fetch_raw", _boom)
    emails, error = gmail.fetch_emails(_config())
    assert emails == []
    assert "gmail down" in error


def test_fetch_emails_skips_malformed_message(monkeypatch):
    good = {
        "id": "g", "threadId": "tg", "internalDate": "1752652800000",
        "payload": {"mimeType": "text/plain", "headers": [], "body": {"data": "aGk="}},
    }
    bad = {"id": "b", "internalDate": "not-a-number", "payload": {"headers": []}}  # int() raises -> skipped

    monkeypatch.setattr(gmail.GmailClient, "fetch_raw", lambda self: [good, bad])
    emails, error = gmail.fetch_emails(_config())
    assert error is None
    assert [e.id for e in emails] == ["g"]
```

Fixture note: `fetch_emails(replay=True)` reads the most recent `gmail_*.json` in `tests/fixtures/` (via `_latest_fixture`, Step 3), so the test needs no date argument. `load_raw_from_fixture(date)` is the explicit-date variant, exercised indirectly.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_fetch_emails.py -v`
Expected: FAIL — `fetch_emails` not defined.

- [ ] **Step 3: Implement fixtures and orchestrator**

Add to the top imports of `app/providers/gmail.py`:

```python
import glob
import json
from pathlib import Path

from app.config.loader import Config
from app.providers._sanitize import sanitize_message
from app.schemas.email import RawEmail
```

Append to `app/providers/gmail.py`:

```python
FIXTURE_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures"


def load_raw_from_fixture(date: str) -> list[dict]:
    path = FIXTURE_DIR / f"gmail_{date}.json"
    return json.loads(path.read_text())


def _latest_fixture() -> list[dict]:
    matches = sorted(glob.glob(str(FIXTURE_DIR / "gmail_*.json")))
    if not matches:
        raise FileNotFoundError("No gmail_*.json fixture found for replay.")
    return json.loads(Path(matches[-1]).read_text())


def record_raw(raw: list[dict], date: str) -> None:
    (FIXTURE_DIR / f"gmail_{date}.json").write_text(json.dumps(raw, indent=2))


def fetch_emails(
    config: Config,
    *,
    replay: bool = False,
    record: bool = False,
) -> tuple[list[RawEmail], str | None]:
    """Fetch and sanitize email. Returns (emails, error_or_None)."""
    try:
        if replay:
            raw = _latest_fixture()
        else:
            raw = GmailClient(config.gmail).fetch_raw()
            if record:
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                record_raw(raw, today)
    except Exception as exc:  # noqa: BLE001 — degrade gracefully per §3.1.1
        log.warning("gmail.fetch_failed", error=str(exc))
        return [], f"Gmail fetch failed: {exc}"

    emails: list[RawEmail] = []
    for msg in raw:
        try:
            emails.append(sanitize_message(msg, config.gmail.max_body_chars))
        except Exception as exc:  # noqa: BLE001 — skip one bad message, not the batch
            log.warning("gmail.sanitize_skip", message_id=msg.get("id"), error=str(exc))
    return emails, None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_fetch_emails.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Run the full suite and linter**

Run: `uv run pytest tests/ -v && uv run ruff check app tests`
Expected: all tests PASS; ruff reports no errors.

- [ ] **Step 6: Commit**

```bash
git add app/providers/gmail.py tests/fixtures/gmail_2026-07-16.json tests/integration/test_fetch_emails.py
git commit -m "feat(gmail): fixture helpers and fetch_emails orchestrator"
```

---

## Notes for the Implementer

- **Run everything with `uv run`** — the project uses uv, not a bare venv.
- **Import order matters for ruff `I`.** When appending to `_sanitize.py` and `gmail.py`, move all new imports up to the module's import block rather than mid-file; the code shown groups them for readability, not final placement.
- **`# noqa: BLE001`** on the two broad excepts in Task 7 is intentional — graceful degradation (specs.md §3.1.1) requires catching everything. Do not narrow them.
- **Fixtures store raw responses only.** Never commit a fixture containing sanitized output.
