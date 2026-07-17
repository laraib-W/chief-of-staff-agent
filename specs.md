# Morning Chief-of-Staff Agent — Technical Specification

**Version:** 1.1
**Date:** 15 July 2026


---

## 1. Purpose

This document is the single source of truth for the Morning Chief-of-Staff Agent's
technical behavior. It translates the v1.1 Design & Build Plan into implementable
contracts: data schemas, API interactions, LLM prompt boundaries, digest format,
configuration surface, and observable guarantees. Everything a developer needs to
build, test, or debug any node in the pipeline lives here.

---

## 2. System Overview

The agent is a LangGraph `StateGraph` with eight nodes arranged in four stages.
It executes once per morning (default 08:00 local), reads three external sources,
applies deterministic rules and LLM judgment, and delivers a single prioritized
HTML digest via email-to-self.

**Invariants that hold across all of v1:**

- Strictly read-only against every external system (Gmail, Google Calendar, Plane).
- Single-user, single-config. No multi-tenancy.
- Fixed graph topology — no ReAct loops, no dynamic tool selection.
- Every claim in the digest carries cited evidence (issue ID, quoted ask, timestamp).

### 2.1 Execution Model

The agent is a **scheduled batch job**, not a long-running service. An
OS-native scheduler (`cron`, `launchd`, systemd timer, or Windows Task
Scheduler) invokes `python -m app.run` at the time set in `config.yaml →
identity.run_time`. A single run completes in under 90 seconds (§11) and
exits. There is no daemon, no background loop, and no inbound network
listener.

Setting up the schedule is the user's responsibility during first-time
setup — the agent itself does not self-schedule.

### 2.2 Delivery Model

The user has **no frontend and no API**. In v1 the user both installs the
agent and reads its output; there is no separate "end user" role.

Two interfaces exist:

- **Email digest** — the daily interface. A single HTML email delivered to
  `config.identity.delivery_address` each morning.
- **CLI** (`python -m app.run`, `--replay`, `--dry-run`, `--config`) — the
  setup and debugging interface. Used during install, tuning, and
  troubleshooting; not part of the daily loop.

---

## 3. Pipeline Stages and Node Contracts

### 3.1 Stage 1 — Sensor Nodes (parallel fan-out from START)

All three sensor nodes execute concurrently. Each writes to its own state key,
so no reducer conflicts arise at fan-in.

#### 3.1.1 `fetch_emails`

- **Type:** Deterministic
- **Input state keys:** (none — reads config and calls Gmail API)
- **Output state key:** `emails: list[RawEmail]`
- **Error state key:** `errors["gmail"]: str | None`
- **Behavior:**
  1. Query Gmail API for messages received in the last `config.gmail.fetch_window_hours`
     hours (default: 24).
  2. Apply the tiered sender filter:
     - Tier 1 — Explicit allowlist (`config.gmail.allowlist`): auto-trusted.
     - Tier 2 — Domain rules (`config.gmail.trusted_domains`, e.g. `@arbisoft.com`): auto-trusted.
     - Tier 3 — Unknown senders: passed to a lightweight LLM call ("Is this a real
       human with a real ask?"). Not dropped silently.
  3. Sanitize every message before writing to state: strip quoted reply chains,
     signatures, HTML tags, tracking links. Preserve sender, subject, clean body text,
     and date.
- **Failure mode:** If the Gmail API call fails or the OAuth token is expired, write
  a descriptive string to `errors["gmail"]` and set `emails` to an empty list.
  The pipeline continues.
- **Auth:** OAuth 2.0 with `gmail.readonly` scope. Refresh token stored via
  `keyring`. Client secret in `.env`.

#### 3.1.2 `fetch_calendar`

- **Type:** Deterministic
- **Input state keys:** (none)
- **Output state key:** `calendar_events: list[CalendarEvent]`
- **Error state key:** `errors["calendar"]: str | None`
- **Behavior:**
  1. Fetch today's events in full detail from the Google Calendar API.
  2. Fetch a 7-day look-ahead (summary-level: title, time, attendees).
  3. Include invitations with `responseStatus == "needsAction"` as pending invites.
- **Failure mode:** Same pattern — error string + empty list.
- **Auth:** OAuth 2.0 with `calendar.readonly` scope, same consent screen as Gmail.
  Fallback: the calendar's secret ICS URL if OAuth becomes untenable.

#### 3.1.3 `fetch_plane`

- **Type:** Deterministic
- **Input state keys:** (none)
- **Output state key:** `plane_issues: list[PlaneIssue]`
- **Error state key:** `errors["plane"]: str | None`
- **Behavior:**
  1. For each project in `config.plane.project_ids`, call the Plane REST API
     (`GET` only) to retrieve issues, states, assignees, due dates, and activity
     timestamps.
  2. Derive the people list from issue assignments automatically. Filter out
     any assignee in `config.plane.ignore_list`.
  3. For each person, compute: issues in progress (with age-in-state), overdue items,
     last activity timestamp, and completions in the rolling window
     (`config.plane.rolling_window_days`, default: 7).
- **Failure mode:** Same pattern.
- **Auth:** Personal API token in `.env`. GET-only usage.
- **Note:** Confirm whether the Plane instance is Cloud or self-hosted; the base URL
  differs (`api.plane.so` vs. custom domain).

---

### 3.2 Stage 2 — Judgment Nodes (one per branch)

#### 3.2.1 `classify_emails`

- **Type:** LLM
- **Input state key:** `emails`
- **Output state key:** `email_actions: list[EmailAction]`
- **Schema (`EmailAction`):**
  ```
  sender: str
  subject: str
  classification: "needs_reply" | "waiting" | "fyi" | "ignore"
  ask: str | None          # the concrete thing being asked
  deadline: date | None    # extracted or inferred deadline
  urgency: "high" | "medium" | "low"
  confidence: float        # 0.0–1.0
  ```
- **LLM call strategy:** Single batched call — all emails classified in one request
  with a strict JSON schema. Switch to per-email calls only if quality degrades.
- **Validation:** Pydantic model validates every item. On validation failure: retry
  once with the same prompt, then fall back to a conservative default
  (`classification="fyi"`, `confidence=0.0`).
- **Domain memory interaction:** Check `seen_email_ids` in domain memory. Emails
  already flagged in a prior run are not re-surfaced.

#### 3.2.2 `analyze_day`

- **Type:** Deterministic (no LLM)
- **Input state key:** `calendar_events`
- **Output state key:** `day_analysis: DayAnalysis`
- **Schema (`DayAnalysis`):**
  ```
  focus_blocks: list[TimeBlock]    # gaps ≥ 60 min with no meetings
  conflicts: list[ConflictPair]    # overlapping events
  back_to_back: list[Stretch]      # 3+ consecutive meetings with < 15 min gaps
  pending_invites: list[Invite]
  unusual_events: list[Event]      # outside normal working hours or new recurring
  week_glance: list[DaySummary]    # next 7 days, one line each
  ```
- **All logic is rule-based.** Thresholds (e.g., what counts as a "gap") come from
  `config.yaml`.

#### 3.2.3 `assess_team`

- **Type:** Rules + LLM
- **Input state key:** `plane_issues`
- **Output state key:** `team_health: list[PersonHealth]`
- **Schema (`PersonHealth`):**
  ```
  person: str
  status: "attention" | "watch" | "on_track"
  in_progress_count: int
  overdue_items: list[IssueRef]
  inactive_items: list[IssueRef]      # no activity ≥ N days
  on_time_rate: float | None          # None if no due-dated completions
  completions_no_due_date: int        # reported separately, never included in rate
  last_activity: datetime
  summary: str                        # LLM-generated, one line, evidence-grounded
  ```
- **Status rules (deterministic, config-driven):**

  | Status    | Rule                                                                                     |
  |-----------|------------------------------------------------------------------------------------------|
  | Attention | Any overdue in-progress issue, OR any in-progress issue inactive ≥ N days (default: 4).  |
  | Watch     | In-progress load > multiplier × team average (default: 1.5×), OR 2+ items due within 7d. |
  | On track  | Everything else.                                                                         |

- **LLM role:** Writes the one-line summary sentence per person only. Numbers are
  never LLM-generated. The summary must reference specific issue IDs.
- **Data honesty:** Issues without due dates cannot count toward or against
  `on_time_rate`. They are reported in `completions_no_due_date`.
- **Framing:** Work-item-centric, not person-judging. "PROJ-142 has been inactive
  5 days" — not "Sara is behind."

---

### 3.3 Stage 3 — Correlation (fan-in)

#### 3.3.1 `correlate`

- **Type:** LLM
- **Input state keys:** `email_actions`, `day_analysis`, `team_health`
- **Output state key:** `top_priorities: list[Priority]`
- **Schema (`Priority`):**
  ```
  rank: int                # 1–5
  headline: str            # one-line summary
  evidence: list[EvidenceRef]   # issue IDs, email subjects, event titles
  source_types: list[str]       # which sources contributed ("email", "calendar", "plane")
  connection: str | None        # cross-source narrative if applicable
  ```
- **Pre-filter:** Before the LLM call, a deterministic ranking step trims each source
  to approximately the top 10 items by urgency/recency. This bounds cost and
  improves reasoning quality.
- **Hard cap:** Exactly 5 priorities. If the LLM returns fewer, pad with the
  highest-urgency un-correlated items. If more, truncate.
- **Core value:** Recognizing that an email about a blocker, a stuck board issue,
  and a 2 PM meeting are the same underlying problem — and saying so in one line.
- **Observations only.** No recommendations, no proposed actions. "Sara has 6
  in-progress issues; team average is 3" — not "reassign two issues."

---

### 3.4 Stage 4 — Render & Deliver

#### 3.4.1 `render_and_deliver`

- **Type:** Deterministic
- **Input state keys:** `day_analysis`, `email_actions`, `team_health`,
  `top_priorities`, `errors`
- **Output state key:** `digest_html: str`
- **Behavior:**
  1. Render the digest as HTML using Jinja2 templates from `app/templates/`.
  2. Send via Gmail API (or SMTP) as an email to `config.identity.delivery_address`.
  3. Insert a row into `runs.db.runs` with timestamps, per-node durations,
     total token usage, errors, and `config_hash`. On successful delivery, insert
     the rendered `digest_html` into `memory.db.digest_log` with `run_id` set to
     the just-created runs row's id.
- **Digest section order (fixed):**
  1. **Today's schedule** — meetings with prep flags (e.g., "the client emailed
     about milestone 2 and you haven't replied — it will come up at 10 AM"), focus
     blocks, conflicts, pending invites, one-line week glance.
  2. **Needs your reply** — emails classified as `needs_reply`, with extracted ask
     and deadline. No reply drafts (read-only).
  3. **Team board** — one card per person: status badge, in-progress count,
     overdue/stuck items with day counts, on-time completion rate.
  4. **Connections spotted** — cross-source correlations with evidence chains.
- **Partial-failure banner:** If any key in `errors` is non-null, a visible banner
  appears at the top: "⚠ Board data unavailable today" (or equivalent).
- **Collapsed overflow:** Top 5 items are expanded. Everything else is collapsed
  into a single summary line.

---

## 4. State Schema (TypedDict)

```python
class AgentState(TypedDict):
    # Stage 1 — raw sensor output
    emails: list[RawEmail]
    calendar_events: list[CalendarEvent]
    plane_issues: list[PlaneIssue]

    # Stage 2 — processed judgments
    email_actions: list[EmailAction]
    day_analysis: DayAnalysis
    team_health: list[PersonHealth]

    # Stage 3 — correlated output
    top_priorities: list[Priority]

    # Cross-cutting
    errors: dict[str, str | None]    # keyed by sensor name
    digest_html: str                 # final rendered output
```

Each branch writes only to its own keys. LangGraph's fan-in requires no custom
reducers because there are no key collisions.

---

## 5. Persistence Architecture

Three separate SQLite files, deliberately not merged:

| File               | Purpose                         | Managed by                  |
|--------------------|---------------------------------|-----------------------------|
| `checkpoints.db`   | LangGraph per-run resume        | `SqliteSaver` (LangGraph)   |
| `memory.db`        | Domain memory across runs       | `app/storage/memory.py`     |
| `runs.db`          | Audit log of every run          | `app/storage/runs.py`       |

**Conventions:**

- Timestamps stored as ISO 8601 UTC in TEXT columns (`YYYY-MM-DD HH:MM:SS`).
  Conversion to `identity.timezone` happens at render time.
- Dates stored as ISO TEXT (`YYYY-MM-DD`).
- JSON blobs stored in TEXT columns with `CHECK(json_valid(...))` (JSON1 ships
  with SQLite ≥ 3.9).
- The `digest_html` is stored **only** in `memory.db.digest_log`. `runs.db.runs`
  links to it via `run_id` (soft cross-database reference) to avoid duplicating
  the payload.

**Domain memory tables (`memory.db`):**

```sql
-- Prevents re-surfacing emails that were already flagged in a prior run.
CREATE TABLE IF NOT EXISTS seen_emails (
    message_id     TEXT PRIMARY KEY,               -- Gmail message ID
    first_seen_at  TEXT NOT NULL,                  -- ISO 8601 UTC
    classification TEXT NOT NULL                   -- last-known label
        CHECK (classification IN ('needs_reply','waiting','fyi','ignore'))
);

CREATE INDEX IF NOT EXISTS idx_seen_emails_first_seen_at
    ON seen_emails(first_seen_at DESC);

-- Per-issue date when "stuck" status was first detected.
-- Enables "still stuck, day 6" framing across runs.
CREATE TABLE IF NOT EXISTS issue_stuck_since (
    issue_id        TEXT PRIMARY KEY,              -- Plane issue ID, e.g. "PROJ-142"
    stuck_since     TEXT NOT NULL,                 -- ISO date
    last_confirmed  TEXT NOT NULL                  -- ISO date; most recent run that saw it stuck
);

CREATE INDEX IF NOT EXISTS idx_issue_stuck_last_confirmed
    ON issue_stuck_since(last_confirmed);

-- Chronological archive of delivered digests. Single source of truth for digest HTML.
CREATE TABLE IF NOT EXISTS digest_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    delivered_at  TEXT NOT NULL,                   -- ISO 8601 UTC
    run_id        INTEGER NOT NULL,                -- soft ref to runs.db → runs.id
    digest_html   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_digest_log_delivered_at
    ON digest_log(delivered_at DESC);

CREATE INDEX IF NOT EXISTS idx_digest_log_run_id
    ON digest_log(run_id);
```

**Runs table (`runs.db`):**

```sql
-- Audit log of every run: timing, cost, errors. Digest HTML lives in memory.db.digest_log.
CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT NOT NULL,                 -- ISO 8601 UTC
    finished_at     TEXT,                          -- NULL if the run crashed
    node_durations  TEXT NOT NULL DEFAULT '{}'     -- JSON: {node_name: seconds}
        CHECK (json_valid(node_durations)),
    total_tokens    INTEGER NOT NULL DEFAULT 0,
    errors          TEXT NOT NULL DEFAULT '{}'     -- JSON: {sensor_name: message}
        CHECK (json_valid(errors)),
    config_hash     TEXT NOT NULL                  -- sha256 of active config.yaml
);

CREATE INDEX IF NOT EXISTS idx_runs_started_at
    ON runs(started_at DESC);
```

**`checkpoints.db`:** Fully managed by LangGraph's `SqliteSaver`. Do not define
or migrate tables in this file — treat it as an opaque LangGraph store.

**Migration approach:** No migration framework in v1. Bootstrap runs
`CREATE TABLE IF NOT EXISTS` for every table above on startup. Schema changes
are additive until v2.

---

## 6. Configuration Surface (`config.yaml`)

### 6.1 Location and lifecycle

- **Where it lives:** `config.yaml` at the repository root. An alternate path
  can be passed via `python -m app.run --config path/to/other.yaml` (§8).
- **What ships in git:** `config.example.yaml` (a fully commented template
  with placeholder values).
- **What does NOT ship in git:** `config.yaml` — it holds real identity data
  (`delivery_address`, `allowlist`, `project_ids`). Added to `.gitignore`.
- **Validation:** the loader parses this file into a Pydantic model at
  startup. Missing required keys, unknown keys, or type errors cause the
  process to exit before any external call is made.
- **Reproducibility:** a sha256 of the resolved config is written to
  `runs.config_hash` (§5) so every run can be traced back to the exact config
  that produced it.

### 6.2 Full example

```yaml
identity:
  user_name: "Awais"
  timezone: "Asia/Riyadh"
  delivery_address: "awais@example.com"
  run_time: "08:00"

gmail:
  allowlist:
    - "ceo@client.com"
  trusted_domains:
    - "arbisoft.com"
  fetch_window_hours: 24

plane:
  base_url: "https://api.plane.so"   # or self-hosted URL
  project_ids:
    - "proj-abc-123"
  ignore_list: []
  rolling_window_days: 7

thresholds:
  inactivity_days: 4
  overdue_grace_days: 0
  load_multiplier: 1.5
  due_soon_horizon_days: 7

llm:
  model: "claude-sonnet-4-6"
  max_tokens_per_node: 4096
  batch_mode: true             # false = per-item calls
```

### 6.3 Key reference

**`identity`** — who the user is and where the digest goes.

| Key                 | Type   | Default   | Purpose                                                                                     |
|---------------------|--------|-----------|---------------------------------------------------------------------------------------------|
| `user_name`         | string | required  | Salutation in the digest ("Good morning, Awais").                                           |
| `timezone`          | string | required  | IANA zone (e.g. `Asia/Riyadh`). Used to render stored UTC timestamps and interpret `run_time`. |
| `delivery_address`  | string | required  | Email address the digest is sent to (§3.4.1).                                               |
| `run_time`          | string | `"08:00"` | Local time the user's OS scheduler should invoke `python -m app.run`. The agent itself does not self-schedule (§2.1). |

**`gmail`** — how emails are filtered before entering the pipeline (§3.1.1).

| Key                   | Type          | Default | Purpose                                                                       |
|-----------------------|---------------|---------|-------------------------------------------------------------------------------|
| `allowlist`           | list[string]  | `[]`    | Tier-1 senders auto-trusted with no LLM check.                                |
| `trusted_domains`     | list[string]  | `[]`    | Tier-2 domains auto-trusted (e.g. `arbisoft.com`).                            |
| `fetch_window_hours`  | int           | `24`    | How far back to query Gmail on each run.                                      |
| `max_body_chars`      | int           | `2000`  | Body length cap applied during sanitization (SECURITY.md §3).                 |
| `max_unknown_sender_llm_calls` | int  | `20`    | Per-run cap on Tier-3 unknown-sender LLM calls (§7). Senders past the cap are tagged `unchecked`, never dropped. |

**`plane`** — which projects to read and how to derive people (§3.1.3).

| Key                     | Type          | Default              | Purpose                                                                     |
|-------------------------|---------------|----------------------|-----------------------------------------------------------------------------|
| `base_url`              | string        | `https://api.plane.so` | Plane Cloud or self-hosted API root.                                      |
| `project_ids`           | list[string]  | required             | Projects the agent will fetch issues from.                                  |
| `ignore_list`           | list[string]  | `[]`                 | Assignee names/IDs to exclude from team assessment.                         |
| `rolling_window_days`   | int           | `7`                  | Window used to count completions per person.                                |

**`thresholds`** — deterministic rules for team health (§3.2.3) and the
schedule (§3.2.2).

| Key                       | Type  | Default | Purpose                                                                                 |
|---------------------------|-------|---------|-----------------------------------------------------------------------------------------|
| `inactivity_days`         | int   | `4`     | Days without activity after which an in-progress issue triggers `attention` status.     |
| `overdue_grace_days`      | int   | `0`     | Days past due before an issue is flagged overdue. `0` = flag immediately.               |
| `load_multiplier`         | float | `1.5`   | `watch` status when a person's in-progress count > multiplier × team average.           |
| `due_soon_horizon_days`   | int   | `7`     | Window used for the "2+ items due within Nd" watch rule.                                |

**`llm`** — model selection and call shape (§7).

| Key                     | Type  | Default             | Purpose                                                                              |
|-------------------------|-------|---------------------|--------------------------------------------------------------------------------------|
| `model`                 | string| `claude-sonnet-4-6` | Provider model string.                                                               |
| `max_tokens_per_node`   | int   | `4096`              | Hard cap on tokens per LLM call. Guards cost and latency.                            |
| `batch_mode`            | bool  | `true`              | `true` = one batched call per node; `false` = per-item calls (higher call count, same total tokens). |

### 6.4 Credentials

**Credentials never appear in this file.** The full credential inventory —
what goes in `.env`, what goes in the OS keyring, and how each is provisioned
during first-time setup — is defined in [SECURITY.md](SECURITY.md) §2.1. In
summary: long-lived tokens (Plane API token, Anthropic API key, Google OAuth
client ID + secret) live in `.env`; the Google OAuth **refresh** token is
stored via `keyring` after a one-time browser flow (`python -m app.auth
--setup`).

---

## 7. LLM Call Budget

A typical morning run makes 3–4 LLM calls total:

| Call                      | Mode     | Estimated tokens (input + output) |
|---------------------------|----------|-----------------------------------|
| Unknown-sender check      | Per-item | ~200 per unknown sender           |
| Email classification      | Batched  | ~2,000–4,000                      |
| Person summaries          | Batched  | ~1,000–2,000                      |
| Correlation               | Single   | ~2,000–3,000                      |

If batch mode is disabled (`llm.batch_mode: false`), call count rises to
roughly N_emails + N_people + 2, but total tokens remain similar.

---

## 8. CLI Interface

```
python -m app.run                    # normal morning run
python -m app.run --replay           # replay from fixtures (offline, free, deterministic)
python -m app.run --dry-run          # full pipeline, skip email delivery
python -m app.run --config alt.yaml  # alternate config file
```

`--replay` is a first-class mode, not an afterthought. Real API responses are
recorded once to `tests/fixtures/` and replayed for prompt tuning, threshold
calibration, and regression checks.

---

## 9. Graceful Degradation Rules

| Failure                                  | Behavior                                                          |
|------------------------------------------|-------------------------------------------------------------------|
| Gmail API unreachable / token expired    | Digest ships without email section; banner: "Gmail unavailable."  |
| Calendar API unreachable                 | Schedule section replaced with "Calendar data unavailable today." |
| Plane API unreachable                    | Team board section omitted; banner shown.                         |
| LLM call fails after retry              | Node falls back to deterministic output (raw numbers, no summary).|
| LLM returns malformed JSON              | Pydantic rejects it; retry once; then deterministic fallback.     |
| All three sensors fail                   | Digest still ships with a "no data available" notice.             |

The digest always ships. Silent omission is never acceptable.

---

## 10. Replay Mode Specification

Replay mode is the primary tool for development, prompt tuning, and regression
testing. It must be available from Phase 0 of the build.

- **Recording:** When running against live APIs, raw responses are optionally
  serialized to `tests/fixtures/{sensor}_{date}.json`.
- **Replaying:** `--replay` swaps every provider's API call with a fixture reader.
  The rest of the pipeline (judgment nodes, correlation, rendering) runs identically.
- **Determinism:** Replay runs use a fixed LLM seed (if supported by the provider)
  and a pinned timestamp so that threshold computations are reproducible.
- **Cost:** Replay runs are free (no API calls, no LLM calls if fixtures include
  LLM outputs) or near-free (LLM calls only, no external API calls).

---

## 11. Non-Functional Requirements

- **Latency:** A full morning run should complete in under 90 seconds, dominated by
  LLM call latency. Sensor fetches run in parallel.
- **Cost:** Target < $0.10 per morning run at Claude Sonnet pricing.
- **Reliability:** The digest must be delivered even on partial failures. Zero silent
  data loss.
- **Privacy:** Email bodies are sanitized before entering agent state. No raw email
  content is stored in SQLite. Credentials never appear in logs, state, or git.
- **Observability:** The `runs` table provides per-node timing, token counts, and
  error traces for every run.
