# /plane-standup — Per-Project Team Snapshot

## Description
Standalone Plane-only view: for each project in `goals.local.yaml
→ plane.projects`, classify state names into semantic buckets,
extract every active issue, resolve assignees to display names, and
produce two sections:

1. **PER-PERSON ACTIVE-STATE COUNTS** — for each assignee, a breakdown
   of how many issues sit in each active state (e.g. `In Progress=3,
   Ready for test=1`).
2. **TEAM HEALTH** — per-person card grouped into `attention` /
   `watch` / `on_track` tiers, with the specific stuck-issue IDs.

Faster than `/gm` when you only care about project state (e.g. before
a standup).

## Preconditions
- Plane MCP connected (`claude mcp list` shows `plane` connected).
- `goals.local.yaml` contains at least one project under `plane.projects`
  (run `/plane-setup` if not).
- `goals.yaml` defines `thresholds.inactivity_days`.

## Instructions

### Step 1: Ground the clock

Use today's date from the environment (already available to Claude). If
a calendar MCP is connected, use its `get_current_time`-style tool to
confirm the timezone from `goals.yaml → identity.timezone`.

### Step 2: Read config

- From `goals.yaml`: `thresholds.inactivity_days`.
- From `goals.local.yaml`: `plane.projects` (list of `{id, name}`).

Also fix this value inline (mirror `app/config`; not yet in
`goals.yaml`):
- `load_multiplier = 1.5` — Watch tier fires when a person's
  active-issue count > this × team avg.

### Step 3: Fan out fetches in parallel

In a **single tool-call batch**, issue:
- `mcp__plane__get_projects` (once — returns identifiers like
  `ARBISOFTOPEN` needed for `issue_id` construction and for Step 5's
  hydration calls).
- `mcp__plane__list_states` for each project in `plane.projects`.
- `mcp__plane__list_project_issues` for each project in `plane.projects`.

**Do not** fetch `mcp__plane__get_workspace_members` yet — Step 6
defers that until we know which UUIDs actually appear as assignees.
The workspace typically has hundreds of members but a single project's
active issues involve <30; resolving the full member list wastes a
subagent round-trip and inflates context.

State + projects payloads are small (~15KB and ~5KB) and can be parsed
inline. Issue payloads commonly overflow to disk (200KB+); never read
them into main context.

Build a `project_id → identifier` map from the `get_projects` result,
filtered to the configured project IDs.

### Step 4: Classify each state (per project) — Claude does this inline

Plane's built-in `state_group` (`backlog / unstarted / started /
completed / cancelled`) doesn't reliably match how teams reason about
"is this work actively in flight?" — for example, `Ready for test` is
grouped under `backlog` in Plane's defaults but is really dev-done work
waiting on QA and can go stale there.

For each project, read the list of state `name`s returned by
`list_states` and assign each one to exactly one of three semantic
buckets:

- **`pipeline`** — not being worked on yet. Typical names: `Backlog`,
  `New`, `Ready`, `Todo`, `To Do`, `Postponed`, `On Hold`.
- **`active`** — being worked on OR waiting on a specific action to
  unblock it. Typical names: `In Progress`, `In progress`,
  `Ready for test`, `In Review`, `Under Review`, `QA`, `Blocked`,
  `Needs Info`, `Waiting on ...`.
- **`terminal`** — closed out. Typical names: `Done`, `Closed`,
  `Archived`, `Cancelled`, `Rejected`, `Completed`.

Rules of thumb when unsure:
- Anything review- or blocker-shaped (`Ready for test`, `In Review`,
  `Needs Info`, `Blocked`) is **active**, even if Plane's `state_group`
  says otherwise. The whole point of the "stuck" signal is to catch
  work that has stalled in these states.
- Anything explicitly paused (`Postponed`, `On Hold`) is **pipeline**,
  not `active` — by definition it's not stuck.
- When still ambiguous, prefer **active** over `pipeline` (better to
  over-flag than to miss stuck work).

Emit a `state_id → {name, bucket}` map for each project. This replaces
Plane's `state_group` for the rest of the pipeline. There is no
`state_group_overrides` config anymore — Claude does the classification
per run from the state names it sees.

### Step 5a: Parse + filter (subagent)

For each project, parse the `list_project_issues` payload. If it
overflowed to disk (typical), delegate to a general-purpose subagent.
Give it:
- the file path,
- the `state_id → {name, bucket}` map from Step 4,
- the `project_id → identifier` map from Step 3,
- the parse + filter rules below.

**Subagent job — parse + filter only.** For each issue in the payload:
- Look up the state via `state_id → {name, bucket}`.
- Drop it if `bucket == "terminal"` OR `bucket == "pipeline"` (only
  `active` survives — this is the standup's focus).
- Emit a stub record: `{issue_id, title, state_name, bucket,
  updated_at, sequence_id, project_identifier}`.

`issue_id` = `f"{project.identifier}-{sequence_id}"` (Plane's short
ID, e.g. `ARBISOFTOPEN-502`).

The subagent returns `per_project_stubs: {project_id → [stub, ...]}`.
Typical size: <50 stubs per project. Compact enough for main context.

### Step 5b: Hydrate active issues (parent agent, one parallel batch)

Hydration must run in the **parent agent**, not the subagent, because
subagent tool-call parallelism has proven unreliable in practice (a
"5-10 concurrent" instruction serialized to ~16s/call in a prior
run). The parent agent fires them in **one tool-call batch** and the
runtime executes them concurrently. Expected wall-clock: 5-15s for a
typical <50-issue set.

For every stub across all projects, in a single tool-call batch,
call `mcp__plane__get_issue_using_readable_identifier` with:
- `project_identifier` = the stub's `project_identifier`,
- `issue_identifier` = `str(sequence_id)`.

For each response, extract `assignees`, `target_date`, `labels`, and
(if present) `state_updated_at`. Merge onto the stub. Apply
`goals.yaml → plane.ignore_list` (if defined) against the resolved
assignees; drop the issue entirely if no assignees remain.

**Derive per issue:**
- `age_in_state_days` = days since `state_updated_at` if present,
  else since `updated_at`. If both absent, use 0.
- `is_stuck` = `bucket == "active" AND age_in_state_days >=
  inactivity_days`.

**If a single hydration call fails** (transient error), keep the
issue with `assignee_ids = []` (buckets to `(unassigned)`) and
continue — never abort the whole run. Track a `hydration_failures`
count for Step 7's banner.

**Final per-issue record schema:**
```
{issue_id, title, state_name, bucket, assignee_ids, updated_at,
 state_updated_at (nullable), age_in_state_days, is_stuck,
 target_date (nullable), labels}
```

Also collect `assignee_uuid_union` — the union of all `assignee_ids`
across kept issues.

**Context-cost ceiling.** Each hydrated payload is ~1-2KB after
extraction, so 50 issues ≈ 100KB in main context — fine. If a run
ever exceeds ~150 active issues, main context bloats; at that point
move hydration back to a subagent and accept the wall-clock cost, or
shard by project across multiple subagents. Today's ceiling: ~50
active issues per run.

**Hydration cost note.** Hydration is N+1 API calls where N =
active-bucket count. It scales with in-flight work, not workspace
size. If/when the upstream `@makeplane/plane-mcp-server` PR stops
stripping fields from `list_project_issues`, delete Step 5b entirely
— Step 5a's stub already carries everything needed once assignees
are present.

### Step 6: Member resolution (issue-first)

Now that Step 5b has surfaced `assignee_uuid_union`, resolve display
names for **only those UUIDs**.

**Early-exit rule — skip this step entirely if `assignee_uuid_union`
is empty across all projects** (e.g. every kept issue is genuinely
unassigned, or hydration failed for everything).

Otherwise call `mcp__plane__get_workspace_members` and hand the
(overflow) file to a general-purpose subagent with the UUID
whitelist. Ask it to return a compact `{uuid: display_name}` JSON
containing **only entries in the whitelist**.

Members schema shape (verified from prior runs): each row has `id`
(uuid, may need `member.id`), `display_name`, `first_name`,
`last_name`, `email`. Fallback order for display: `display_name` →
`first_name last_name` → `email` → uuid.

### Step 7: Team extraction from issues

Fan each kept issue across every assignee (co-assigned issues count for
each carrier). If an issue has no assignees (either genuinely
unassigned or hydration failed for that issue), bucket it under
`(unassigned)`.

**Hydration-failure banner.** If Step 5 reported
`hydration_failures > 0`, prepend a one-line banner to the report:

> WARNING: Plane hydration failed for N of M active issues — those
> fall under (unassigned). Standup shipping anyway.

Never silently swallow the standup; ship it with the banner instead.

### Step 8: Build per-person cards

For each person_id (assignee uuid, or the synthetic `(unassigned)`):

- `active_count` = count of the person's kept issues (all of them are
  `bucket == active` by Step 5's filter).
- `state_breakdown` = `{state_name: count}` across the person's issues
  — e.g. `{"In Progress": 3, "Ready for test": 1}`.
- `stuck_items` = list of issues where `is_stuck`.
- `last_activity` = max `updated_at` across the person's issues.

### Step 9: Classify tier

- `attention` — `stuck_items` non-empty.
- `watch` — otherwise, if `active_count > load_multiplier × team_avg`.
- `on_track` — everything else.

`team_avg` = mean of `active_count` across people whose count > 0.

### Step 10: Report

For each project print two sections:

```
======================================================================
 PROJECT: <name>
======================================================================

--- PER-PERSON ACTIVE-STATE COUNTS (<n_people>) ---
  <person>              <state>=<n>, <state>=<n>, ...
  <person>              <state>=<n>, ...

--- TEAM HEALTH (<n_people>) ---

  --- ATTENTION ---
  <person>              active=<n>  stuck=<n>
      stuck: <ID-1>(<days>d), <ID-2>(<days>d)

  --- WATCH ---
  <person>              active=<n>

  --- ON_TRACK ---
  <person>              active=<n>
```

- Sort within each tier by `active_count desc`, then by name.
- Person name column is left-aligned to width 22.
- Omit the `stuck:` sub-line when `stuck` is 0.
- Omit an entire tier heading if it has no cards.

If a project has zero active-bucket issues after Step 5's filter,
print `(all clear)` under that project header and move on.

End the whole report with a totals line across projects:
`TOTALS: people=<n>, active=<n>, stuck=<n>`.

## Guardrails

- **Read-only.** No `mcp__plane__update_*`, `create_*`, `delete_*`,
  or `add_*_comment` calls. If Claude even considers one, stop and
  explain why.
- **Context hygiene.** Never read raw `list_project_issues` or
  `get_workspace_members` overflow files into main context — always
  route them through a subagent. Main context should only ever see
  compact records (per-issue dicts, filtered member map).
- Classification is Claude's judgment based on state *names*, not
  Plane's `state_group`. State-group-based filtering is a legacy
  concept; treat it as advisory only.
- If Plane MCP fails mid-run, ship the digest anyway with a one-line
  banner naming the source that failed. A missing sensor is never a
  reason to skip the run.
- If hydration fails for some issues, ship the digest with a banner
  naming the count rather than silently bucketing them to
  `(unassigned)`.
- `list_project_issues` in `@makeplane/plane-mcp-server` v0.1.5
  strips `assignees`, `target_date`, `labels`, and `state_updated_at`
  from every issue — Step 5's hydration works around this. If the
  upstream PR to stop stripping ever lands, the hydration sub-step
  can be deleted with no other changes required.
