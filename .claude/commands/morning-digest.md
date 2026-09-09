# /morning-digest — Morning Digest

## Description
Fetch the last 24h of email, today's calendar (+7-day look-ahead), and
current Plane state across configured projects. Rank the top priorities,
list what needs a reply, surface team-health flags, and deliver one HTML
digest to the user's delivery address.

Equivalent to `python -m app.run` on `main`, but with Claude driving the
tool sequencing and composition instead of a fixed LangGraph pipeline.

## Preconditions
- MCP servers `gmail`, `gcal`, and `plane` are connected (see
  `docs/mcp-servers.md`).
- `goals.yaml` at repo root defines `thresholds` and
  `identity.delivery_address`; `goals.local.yaml` defines
  `plane.projects` (run `/plane-setup` if not).
- The user has authorized delivery for this run. If invoked in dry-run
  mode (from the SDK entrypoint), **do not call `send-email`** — print the
  finished HTML to the transcript instead.

## Instructions

Follow the steps in order. Do **not** skip step 0 — every downstream
calculation (today's date, staleness) depends on it.

### Step 0: Ground the clock

Call the calendar MCP's `get-current-time` (or equivalent) to get the
authoritative current time and timezone. Extract:
- Today's date in ISO format
- Day of week
- Timezone

Never guess the day of week from your knowledge cutoff.

### Step 1: Read config

From `goals.yaml`:
- `thresholds.inactivity_days`
- `plane.ignore_list` (optional; passed through to plane-fetcher)
- `identity.delivery_address`, `identity.user_name`,
  `identity.timezone`

From `goals.local.yaml`:
- `plane.projects` — list of `{id, identifier, name}`. Each entry
  is one plane-fetcher invocation in Step 2.

The Plane workspace itself is scoped by the plane MCP server's env
vars (see `docs/mcp-servers.md`), so no `workspace_slug` is needed
in main context.

If `goals.yaml` is missing or malformed, stop and tell the user to
run `docs/setup-guide.md` step 3. If `goals.local.yaml` is missing
or `plane.projects` is empty, tell the user to run `/plane-setup`
and stop. If any project entry lacks `identifier`, tell the user to
re-run `/plane-setup` (older setups didn't capture it).

### Step 2: Fan-out fetch (parallel subagents)

Dispatch every fetch through a Sonnet subagent. **Emit every `Agent`
call in one message** so they run concurrently — do not chain them.
Each subagent runs read-only, keeps raw payloads out of this context,
and returns compact structured JSON per its contract.

- **Gmail** → one `Agent` call with `subagent_type: "gmail-fetcher"`.
  Prompt: "Fetch inbox mail newer than 24h and return the structured
  JSON per your contract." Returns `{emails: [...], error}`.
- **Calendar** → one `Agent` call with `subagent_type:
  "calendar-fetcher"`. Prompt must supply `time_min`, `time_max`
  (today 00:00 → today+`calendar_lookahead_days` 23:59, both in the
  user's timezone), and `timezone`. Returns `{events: [...], error}`.
- **Plane** → one `Agent` call with `subagent_type: "plane-fetcher"`
  **per project** in `goals.local.yaml → plane.projects`. Each prompt
  must supply:
  - `project_id` (uuid) and `project_identifier` (short prefix, e.g.
    `ARBISOFTOPEN`) from `goals.local.yaml` — the fetcher will not
    call `mcp__plane__get_projects` on its own.
  - `inactivity_days` from `goals.yaml → thresholds.inactivity_days`
    so the subagent can compute `is_stuck` and
    `age_in_state_days` per issue.
  - `ignore_list` from `goals.yaml → plane.ignore_list` when
    defined; otherwise omit.

  Prompt shape: "Fetch active-bucket issues for project_id=<uuid>,
  project_identifier=<prefix>, inactivity_days=<N>[, ignore_list=[...]]
  and return the structured JSON per your contract." Returns
  `{states, issues, members, hydration_failures, error}`. Each
  issue carries `bucket` (via its `state_id` → `states[].bucket`
  lookup), `age_in_state_days`, and `is_stuck` — the subagent has
  already applied the v0.1.5 hydration workaround, so no extra
  Plane calls are needed in main context.

Subagents return only refs and evidence (msg_ids, event_ids,
issue_ids, state buckets) — never narrative summaries. Ranking and
cross-source correlation stay in this (main) agent because they
require citation-grade access to every raw ref. State classification
into pipeline/active/terminal buckets is done by the plane-fetcher
subagent per project (from state names) and reported back on each
state's `bucket` field — the main agent trusts those buckets and
doesn't reclassify.

If a subagent returns a non-null `error`, record it in
`fetch_errors[<source>]` and proceed with the other sources.

### Step 3: Deterministic rules

Apply before asking the LLM for anything:

- **Stuck:** any issue where `is_stuck == true` on the plane-fetcher
  response. The subagent has already applied the semantics — issue's
  state maps to bucket `active` AND `age_in_state_days >=
  inactivity_days`. This merges the old "overdue" and "inactive"
  signals; `target_date` is rarely set in this workflow, so "sitting
  in an active state too long" is the durable staleness indicator.
  Do not recompute — the subagent is authoritative.
- **Pending invite:** Calendar event with your `response_status ==
  'needsAction'` and start within 24h.
- **Trusted sender:** Email from a domain listed in `goals.yaml
  → gmail.trusted_domains` gets a +1 rank boost.

### Step 4: LLM judgment (your own reasoning)

Now compose:

1. **Top priorities (up to 5, ranked 1 = highest).** Combine signals
   across sources. If an email asks about a stuck Plane issue, that's
   one priority — cite both. Every priority must have `evidence: [...]`
   with concrete refs.
2. **Needs reply.** From today's emails, identify those that contain a
   direct question or request for the user. Quote the ask (≤ 20 words).
   Ignore newsletters, notifications, and auto-generated mail.
3. **Team health.** Group Plane issues (active-bucket only) by
   assignee. Sort people by: `stuck count desc → active count desc`.
   For each person, one line: name, active count, stuck count, and
   the specific issue IDs (with days-in-state) when stuck > 0.

### Step 5: Render HTML

Produce a single HTML string matching the format in `CLAUDE.md` Part 4.
Constraints:
- Inline CSS only (email clients strip `<style>` in `<head>`).
- No external images.
- All refs (`msg_id`, `event_id`, `issue_id`) rendered as plain text so
  the user can grep for them.
- If `fetch_errors` is non-empty, prepend the failure banner:
  "Some sources unavailable today: <comma-separated list>."

### Step 6: Deliver (or dry-run)

- **Normal run:** call the gmail MCP's `send-email` with
  - `to = identity.delivery_address`
  - `subject = "Morning digest — YYYY-MM-DD"`
  - `body = <the HTML>`
  - `content_type = "text/html"` (or the server-specific equivalent)
- **Dry-run:** the SDK entrypoint sets a permission callback that
  denies `send-email`. If you get a denial, print the HTML to the
  transcript with a leading marker `--- DRY-RUN DIGEST ---` and stop.

### Step 7: Report

After delivery (or dry-run print), summarize in ≤ 3 lines to the
transcript:
- Number of priorities, emails processed, calendar events, Plane
  issues, and any `fetch_errors`.
- The message ID returned by `send-email`, or `dry-run` if skipped.

## Guardrails

- **Never `send-email` more than once per run.** If the send fails,
  report the error — do not retry.
- **Never delete, mark-as-read, or reply to email.**
- **Never mutate Plane** (no state changes, comments, assignee edits).
- **Never call `create-event` or `update-event` on the calendar.**
- **Subagents inherit these constraints.** You cannot bypass a
  read-only rule by asking a subagent to do it — the session-level
  deny list still applies to their tool calls.
- If any tool asks for confirmation before a destructive action, the
  answer is always **no** — you are read-only.
