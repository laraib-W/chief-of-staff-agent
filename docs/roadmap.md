# Roadmap

Build phases for the Morning Chief-of-Staff Agent v1. Each phase ships a
working slice of the pipeline and has an exit criterion that gates the next
phase. Phases 1–4 evaluation rituals are detailed in
[EVALUATION.md](../EVALUATION.md) §5.

---

## Phase 0 — Base Scaffold

**Goal:** Every foundational piece a later phase needs — no sensors, no
judgment, no LLM calls, but the whole chassis is running.

**Ships:**

- Project scaffold: `pyproject.toml`, `uv.lock`, directory layout matching
  [CONTRIBUTING.md](../CONTRIBUTING.md) §1, `.gitignore`, `.env.example`.
- LangGraph skeleton: `AgentState` TypedDict, empty `StateGraph`, one
  placeholder node wired end-to-end.
- Storage bootstrap: three SQLite files created on startup with the DDL from
  [specs.md](../specs.md) §5 (`CREATE TABLE IF NOT EXISTS`).
- Config loader: `config.yaml` parsed via Pydantic, fails fast on missing keys.
- Auth CLI: `python -m app.auth --setup` runs the Google OAuth browser flow
  and writes the refresh token to the OS keyring.
- Replay-mode plumbing: fixture recorder and reader, provider-level swap
  toggled by `--replay`.
- CI harness: `ruff format`, `ruff check`, `pytest`, `pip audit` on every PR.

**Exit criterion:** `python -m app.run --dry-run` executes the empty pipeline
end-to-end, writes a row to `runs.db.runs`, and exits 0 in both live and
`--replay` modes.

---

## Phase 1 — Plane Sensor + Team Assessment

**Goal:** The team-board half of the digest is live and trustworthy.

**Ships:**

- `fetch_plane` sensor node (specs §3.1.3).
- `assess_team` node with deterministic status rules + LLM one-line summary
  (specs §3.2.3).
- Team board digest section renders per-person cards.
- Initial `golden_team.jsonl` labels collected during daily reviews.

**Exit criterion:** Status agreement ≥ 90% between the agent and the lead's
independent board read on 3 consecutive days (EVALUATION §2.2).

---

## Phase 2 — Calendar

**Goal:** Today's schedule appears in the digest, correctly.

**Ships:**

- `fetch_calendar` sensor node with the same OAuth consent as Gmail
  (specs §3.1.2).
- `analyze_day` deterministic node: focus blocks, conflicts, back-to-back
  stretches, pending invites, 7-day glance (specs §3.2.2).
- Schedule digest section.

**Exit criterion:** No missed events, incorrect times, or missed conflicts
for 5 consecutive days. OAuth refresh survives a week without manual
re-authentication (EVALUATION §2.4).

---

## Phase 3 — Email Classification

**Goal:** The "Needs your reply" section is safe to act on.

**Ships:**

- `fetch_emails` sensor with the sanitization pipeline from
  [SECURITY.md](../SECURITY.md) §3.
- Tiered sender filter (allowlist, trusted domains, LLM unknown-sender check).
- `classify_emails` batched LLM node with Pydantic validation.
- Needs-your-reply digest section with extracted ask and deadline.
- Initial `golden_emails.jsonl` (sanitized per EVALUATION §3.1).

**Exit criterion:** Zero missed `needs_reply` emails for 7 consecutive days;
false-positive rate below 15% (EVALUATION §2.1).

---

## Phase 4 — Correlation + Full Digest

**Goal:** The agent connects the dots across sources and the lead uses the
digest daily.

**Ships:**

- `correlate` node with deterministic pre-filter + LLM ranking (specs §3.3.1).
- "Connections spotted" digest section with evidence chains.
- Adversarial set of 20–30 tricky inputs (EVALUATION §3.2).
- Final polish: partial-failure banners, overflow collapse, weekly retro
  cadence.

**Exit criterion:** Correlation precision ≥ 80%, zero hallucinated
connections, and two consecutive weeks of daily digest use with action taken
on most mornings (EVALUATION §2.3, §2.5).

---

## After v1

Post-Phase 4 work (v2 write actions, human-approval gates, multi-user
support) is scoped in [SECURITY.md](../SECURITY.md) §7 and out of scope for
this roadmap.
