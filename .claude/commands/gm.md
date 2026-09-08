# /gm — Morning Digest

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
- `goals.yaml` at repo root defines `plane.project_ids`, `thresholds`, and
  `identity.delivery_address`.
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

### Step 1: Read `goals.yaml`

Read the file to get:
- `plane.workspace_slug`, `plane.project_ids`
- `thresholds.inactivity_days`
- `identity.delivery_address`, `identity.user_name`

If the file is missing or malformed, stop and tell the user to run
`docs/setup-guide.md` step 3.

### Step 2: Fan-out fetch (in parallel)

Issue the following tool calls together. Wait for all three before
proceeding. Every failure is caught: record it in a `fetch_errors` dict
keyed by source; do not abort.

- **Gmail** — list emails received in the last 24h. For each, capture
  `id`, `sender`, `subject`, `date`, and a body snippet (~500 chars).
  Skip drafts and sent mail.
- **Calendar** — list events for today (00:00–23:59 in the user's
  timezone) plus the next 7 days. Capture `id`, `summary`, `start`,
  `end`, `attendees`, `response_status`, `location`.
- **Plane** — for each `project_id` in `goals.yaml`, list issues (state,
  assignees, due date, updated_at). Also fetch workspace members so
  assignee IDs can be resolved to names.

### Step 3: Deterministic rules

Apply before asking the LLM for anything:

- **Stuck:** Plane issue in an "active" state (see `/plane-standup`
  Step 4 for the pipeline/active/terminal classification Claude
  performs per project) AND `age_in_state_days >= inactivity_days`.
  This merges the old "overdue" and "inactive" signals — `target_date`
  is rarely set in this workflow, so "sitting in an active state too
  long" is the durable staleness indicator.
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
- If any tool asks for confirmation before a destructive action, the
  answer is always **no** — you are read-only.
