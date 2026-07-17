# Gmail Provider — Design

**Date:** 2026-07-16
**Status:** Approved (brainstorm), pending implementation plan
**Scope:** `app/providers/gmail.py` provider only. The `fetch_emails` LangGraph
node, the tiered sender filter (allowlist / trusted domains / Tier-3 LLM), and
the interactive OAuth consent flow (`app.auth`) are **out of scope** and remain
separate tickets.

References: specs.md §3.1.1, §10; SECURITY.md §2, §3, §4.

---

## 1. Purpose

Build the read-only Gmail provider: authenticate against Gmail, fetch the last
N hours of messages, and run every message through the sanitization pipeline
before it can enter agent state. Output is a list of clean `RawEmail` objects.

The provider never writes to LangGraph state and never sees LangGraph state —
per SECURITY.md §2.2, credentials live in env/keyring, not in `AgentState`. The
provider returns plain values; the (future) node maps them onto state keys.

---

## 2. Scope Decisions

Locked during brainstorming:

- **Provider only.** Node, tiered filter, and Tier-3 unknown-sender LLM check
  are deferred.
- **Read-only credentials loader now.** The provider builds its Gmail client
  from env (client id/secret) + keyring (refresh token) using `google-auth`.
  It implements only the *read* side of credential loading; the interactive
  OAuth consent flow stays in the deferred `app.auth` ticket. The provider is
  runnable against live Gmail when a refresh token already exists in keyring.
- **BeautifulSoup for HTML stripping.** Conscious exception to SECURITY.md §4's
  dependency-minimization rule: real-world email HTML is too messy and hostile
  for the stdlib `html.parser` to extract clean text reliably. Adds
  `beautifulsoup4` (+ `lxml` parser) to `pyproject.toml`. Documented here so the
  tradeoff is explicit and reviewable.

---

## 3. Architecture (Approach A — two-layer)

The API-call layer and the sanitization layer are separated so that the
security-critical sanitization logic is a set of **pure functions** testable
with zero mocks and zero network.

```
app/providers/gmail.py        # GmailClient (I/O) + fetch_emails orchestrator
app/providers/_sanitize.py    # pure sanitization functions (no I/O)
app/schemas/email.py          # RawEmail (fleshed out)
tests/fixtures/gmail_*.json   # recorded raw Gmail responses (replay input)
```

`_sanitize.py` is private to the `providers` package (leading underscore) —
nothing outside needs it.

The **replay seam** is exactly one method: `GmailClient.fetch_raw()`. `--replay`
substitutes a fixture reader for it; everything downstream (sanitization) runs
identically on live and replayed data.

---

## 4. Data Model — `RawEmail`

SECURITY.md §3 defines the sanitized output as `{sender, subject, clean_body,
date}`. Two identity fields are added because `classify_emails` needs the Gmail
message id for `seen_email_ids` deduplication (specs.md §3.2.1), so it must
survive into state.

```python
class RawEmail(BaseModel):
    id: str            # Gmail message id — dedup key downstream
    thread_id: str
    sender: str        # parsed From header
    subject: str
    clean_body: str    # fully sanitized plain text
    date: datetime     # UTC, derived from internalDate
```

`clean_body` never contains raw HTML, tracking metadata, or attachment content
(SECURITY.md §3).

---

## 5. Public Interface (`gmail.py`)

```python
class GmailClient:
    def __init__(self, config): ...
        # holds config; credentials loaded lazily on first fetch

    def fetch_raw(self) -> list[dict]:
        # live Gmail API → raw message dicts.
        # Queries messages received within config.gmail.fetch_window_hours.
        # The ONLY method that --replay swaps.


def load_raw_from_fixture(date: str) -> list[dict]:
    # replay path: read tests/fixtures/gmail_{date}.json

def record_raw(raw: list[dict], date: str) -> None:
    # optional recording of raw responses to a fixture


def fetch_emails(
    config,
    *,
    replay: bool = False,
    record: bool = False,
) -> tuple[list[RawEmail], str | None]:
    # orchestrator:
    #   1. get raw dicts (live via GmailClient.fetch_raw, or fixture)
    #   2. optionally record raw
    #   3. sanitize each raw dict -> RawEmail (skip + log malformed)
    #   4. return (emails, error_string_or_None)
```

`fetch_emails` returns `(emails, error)` so the future `fetch_emails` node maps
the tuple directly onto the `emails` and `errors["gmail"]` state keys per
specs.md §3.1.1. The provider itself stays free of LangGraph.

---

## 6. Sanitization Pipeline (`_sanitize.py`, SECURITY.md §3)

Entry point `sanitize_message(raw: dict) -> RawEmail`:

**Extraction**
- `extract_headers(raw)` → `sender` (From), `subject`, `date` (internalDate →
  UTC `datetime`), `id`, `thread_id`.
- `extract_body(raw)` → prefer the `text/plain` MIME part; fall back to
  `text/html` (which step 1 then strips).

**Body pipeline — six ordered pure functions:**

1. `strip_html` — BeautifulSoup `get_text()`; collapse markup to plain text.
2. `strip_tracking` — remove known tracking domains (e.g. `click.mailchimp.com`,
   sendgrid link-wrappers) and strip UTM query params from URLs. Tracking list
   is a small hardcoded constant in `_sanitize.py` for v1 (not config — YAGNI).
3. `strip_quotes` — cut quoted reply chains: from the first `^>` line, an
   `On … wrote:` block, or an Outlook `-----Original Message-----` separator.
4. `strip_signature` — cut the signature tail: from a `^-- $` delimiter line,
   `Sent from my …`, or a `Regards,\n<name>`-style closing.
5. `normalize_unicode` — `unicodedata` NFC; drop zero-width characters
   (U+200B–200D, U+FEFF) and bidirectional overrides (U+202A–202E, U+2066–2069).
6. `truncate` — cap at `config.gmail.max_body_chars` (default 2000).

**Ordering rationale:**
- HTML strip runs **first** so the quote/signature regexes operate on plain
  text, not markup.
- Truncate runs **last** so the character cap applies to the final clean text,
  not to raw HTML that will shrink.

Each step is an independently unit-tested pure function — this isolation is the
main payoff of Approach A and where the security test coverage concentrates.

---

## 7. Configuration Changes

Add one key to the `gmail` config block (specs.md §6.3, `config.example.yaml`):

| Key                  | Type | Default | Purpose                                            |
|----------------------|------|---------|----------------------------------------------------|
| `gmail.max_body_chars` | int  | `2000`  | Body length cap (SECURITY.md §3 step 6). Guards against token-stuffing. |

SECURITY.md §3 already specifies 2000 as the default and calls it configurable;
this key makes it so.

---

## 8. Error Handling (specs.md §3.1.1 failure contract)

- `fetch_emails(...)` catches **any** live-fetch failure — `HttpError`, expired
  or absent OAuth token, network error — and returns `([], "<descriptive
  string>")`. It never raises to the caller. Graceful degradation: the pipeline
  continues and the digest still ships.
- **Per-message sanitization failures are isolated.** A single malformed message
  is logged (structlog) and skipped; it does not fail the batch.
- **Credential-loading failure** (no refresh token in keyring) produces a clear
  error string that names `python -m app.auth` as the remedy, even though that
  ticket is deferred.
- **No secrets or bodies in logs** (specs.md §11 Privacy). Log counts, message
  ids, and timings only — never tokens or `clean_body`.

---

## 9. Replay / Record (specs.md §10)

- `--replay` → `fetch_emails(replay=True)` reads
  `tests/fixtures/gmail_{date}.json` and runs the *same* sanitization pipeline.
  No network, no credentials required.
- Recording is opt-in (`record=True`) and writes the raw response dicts to the
  fixture file.
- Fixtures store **raw** Gmail responses only. Sanitization is never baked into a
  fixture, so tuning the pipeline is a pure offline replay loop.
- Wiring the `--replay` / `--record` CLI flags is the node/CLI ticket's job. This
  ticket only exposes the seam (`fetch_raw` swap + fixture read/write helpers).

---

## 10. Testing Strategy

- **Unit (bulk of coverage):** each `_sanitize` step in isolation —
  - `strip_html`: nested tags, entities, style/script removal.
  - `strip_tracking`: tracking domains, UTM param stripping.
  - `strip_quotes`: `>` chains, `On … wrote:`, Outlook `-----Original Message-----`.
  - `strip_signature`: `-- ` delimiter, `Sent from my …`, `Regards,` closings.
  - `normalize_unicode`: NFC, zero-width, bidi-override payloads.
  - `truncate`: boundary at the cap.
- **Unit:** `RawEmail` validation; `extract_headers` / `extract_body` from sample
  raw dicts; `text/plain`-over-`text/html` preference.
- **Integration (replay):** `fetch_emails(replay=True)` against a committed
  fixture asserts the sanitized `RawEmail` list. Marked `@pytest.mark.replay`.
- **Error paths:** mocked client raising `HttpError` → asserts `([], error_str)`;
  a malformed message is skipped, not fatal.
- **Live Gmail API is never called in tests** — only mocks and fixtures.

---

## 11. Out of Scope (explicit)

- `fetch_emails` LangGraph node and its state wiring.
- Tiered sender filter (allowlist / trusted domains / Tier-3 unknown-sender LLM).
- Interactive OAuth consent flow (`app.auth`) and refresh-token *acquisition*.
- CLI wiring of `--replay` / `--record` flags.
- Calendar and Plane providers.
