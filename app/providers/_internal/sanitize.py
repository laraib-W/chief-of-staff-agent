"""Pure email sanitization functions — no I/O (SECURITY.md §3)."""

from __future__ import annotations

import base64
import re
import unicodedata
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

from app.schemas.email import RawEmail

TRACKING_DOMAINS: frozenset[str] = frozenset(
    {
        "click.mailchimp.com",
        "list-manage.com",
        "sendgrid.net",
        "mandrillapp.com",
        "sparkpostmail.com",
        "click.e.example.com",
    }
)

_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "blockquote",
        "li",
        "tr",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "ul",
        "ol",
        "table",
        "section",
        "article",
        "header",
        "footer",
        "pre",
    }
)

_URL_RE = re.compile(r"https?://[^\s)>\]]+")
_WS_RE = re.compile(r"[ \t]+")
_TRAILING_PUNCT_RE = re.compile("[.,;:!?\"')\\]}]+$")
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")

_ON_WROTE_RE = re.compile(r"^On .+wrote:$", re.MULTILINE)
_OUTLOOK_RE = re.compile(
    r"^-{2,}\s*Original Message\s*-{2,}$", re.MULTILINE | re.IGNORECASE
)
_SIG_CLOSING_RE = re.compile(
    r"^(regards|best|thanks|cheers|sincerely)[,!.]?$", re.IGNORECASE
)

_INVISIBLE = dict.fromkeys(
    [
        0x200B,
        0x200C,
        0x200D,
        0xFEFF,
        0x202A,
        0x202B,
        0x202C,
        0x202D,
        0x202E,
        0x2066,
        0x2067,
        0x2068,
        0x2069,
    ],
    None,
)


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
        "date": datetime.fromtimestamp(internal_ms / 1000, tz=UTC),
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


def strip_html(text: str) -> str:
    soup = BeautifulSoup(text, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for block in soup.find_all(_BLOCK_TAGS):
        block.append("\n")
    plain = soup.get_text(separator=" ")
    plain = _WS_RE.sub(" ", plain)
    lines = (line.strip() for line in plain.splitlines())
    return "\n".join(line for line in lines if line).strip()


def _clean_url(url: str) -> str | None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if any(host == d or host.endswith("." + d) for d in TRACKING_DOMAINS):
        return None
    kept = [
        (k, v) for k, v in parse_qsl(parsed.query) if not k.lower().startswith("utm_")
    ]
    return urlunparse(parsed._replace(query=urlencode(kept)))


def strip_tracking(text: str) -> str:
    def _sub(m: re.Match) -> str:
        raw = m.group(0)
        trailing_match = _TRAILING_PUNCT_RE.search(raw)
        trailing = trailing_match.group(0) if trailing_match else ""
        url = raw[: len(raw) - len(trailing)] if trailing else raw
        cleaned = _clean_url(url)
        return (cleaned or "") + trailing

    result = _URL_RE.sub(_sub, text)
    return _MULTI_SPACE_RE.sub(" ", result)


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


def normalize_unicode(text: str) -> str:
    return unicodedata.normalize("NFC", text).translate(_INVISIBLE)


def truncate(text: str, max_chars: int) -> str:
    return text[:max_chars]


def sanitize_message(raw: dict, max_body_chars: int) -> RawEmail:
    headers = extract_headers(raw)
    body = extract_body(raw)
    body = strip_html(body)
    body = normalize_unicode(body)
    body = strip_tracking(body)
    body = strip_quotes(body)
    body = strip_signature(body)
    body = truncate(body, max_body_chars)
    return RawEmail(
        id=headers["id"],
        thread_id=headers["thread_id"],
        sender=headers["sender"],
        subject=headers["subject"],
        clean_body=body,
        date=headers["date"],
    )
