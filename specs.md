# Morning Chief-of-Staff Agent — Technical Specification

**Version:** 1.1
**Date:** 15 July 2026
**Owner:** Awais @ Arbisoft

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
  3. Record run metadata to the `runs` SQLite table: timestamp, per-node durations,
     total token usage, errors encountered, and a snapshot of `digest_html`.
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

**Domain memory tables (`memory.db`):**

- `seen_emails` — email message IDs already flagged, to prevent re-surfacing.
- `issue_stuck_since` — per-issue date when "stuck" status was first detected,
  enabling "still stuck, day 6" framing.
- `digest_log` — timestamp and digest snapshot for each delivered digest.

**Runs table (`runs.db`):**

- `id`, `started_at`, `finished_at`, `node_durations` (JSON), `total_tokens`,
  `errors` (JSON), `digest_snapshot` (HTML), `config_hash`.

---

## 6. Configuration Surface (`config.yaml`)

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

**Credentials never appear in this file.** OAuth client secret and Plane API token
live in `.env`; refresh tokens are stored via `keyring`.

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
