# /plane-standup — Per-Project Team Snapshot

## Description
Standalone Plane-only view: for each project in `goals.yaml
→ plane.projects`, produce two sections:

1. **PER-PERSON ACTIVE-STATE COUNTS** — for each assignee, a breakdown
   of how many issues sit in each active state (e.g. `In Progress=3,
   Ready for test=1`).
2. **TEAM HEALTH** — per-person card grouped into `attention` /
   `watch` / `on_track` tiers, with the specific stuck-issue IDs.

Faster than `/morning-digest` when you only care about project state
(e.g. before a standup).

All Plane fetching is delegated to the `plane-fetcher` subagent so
`/plane-standup` and `/morning-digest` share one implementation of
state classification, active-bucket filtering,
and the member cache. This skill's job is to fan out the subagent,
aggregate the responses, and print the report.

## Preconditions
- Plane MCP connected. The user verifies this at setup time; don't
  shell out to check it — just call the tools and degrade if they're
  unavailable.
- `goals.yaml` contains at least one project under
  `plane.projects` with an `identifier` per entry (run `/plane-setup`
  if not).
- `goals.yaml` defines `thresholds.inactivity_days`.

## Instructions

### Step 1: Ground the clock

Use today's date from the environment (already available to Claude).
If a calendar MCP is connected, use its `get_current_time`-style tool
to confirm the timezone from `goals.yaml → identity.timezone`.

### Step 2: Read config

- From `goals.yaml`: `thresholds.inactivity_days`, and
  `plane.ignore_list` if present (list of display-name strings; each
  matching assignee is dropped from every issue).
- From `goals.yaml`: `plane.projects` (list of
  `{id, identifier, name}`).

If any project entry is missing `identifier`, tell the user to
re-run `/plane-setup` (older setups didn't capture it) and stop. Do
NOT call the `project` tool as a fallback (it 404s on self-hosted CE).

Also fix this value inline (mirror `app/config`; not yet in
`goals.yaml`):
- `load_multiplier = 1.5` — Watch tier fires when a person's
  active-issue count > this × team avg.

### Step 3: Fan out plane-fetcher per project

Issue one `Agent` call with `subagent_type: "plane-fetcher"` **per
project**, in a **single tool-call batch** so the runtime
parallelises them. Each prompt must supply:

- `project_id` — the project UUID from `goals.yaml`.
- `project_identifier` — the readable prefix from the same entry.
- `inactivity_days` — from `goals.yaml → thresholds.inactivity_days`.
- `ignore_list` — from `goals.yaml → plane.ignore_list` when defined,
  otherwise omit.

Prompt shape:

> Fetch active-bucket issues for project_id=<uuid>,
> project_identifier=<prefix>, inactivity_days=<N>[, ignore_list=[...]]
> and return the structured JSON per your contract.

The subagent handles state classification, active-bucket filtering,
member resolution, and `age_in_state_days` / `is_stuck` derivation
internally, via `scripts/plane.sh`. Do not repeat that work in main
context.

Each response has shape (see `.claude/agents/plane-fetcher.md` for
the authoritative contract):

```
{
  project_id, project_identifier, total_issues,
  states: [{name, count, bucket}],
  issues: [{issue_id, readable_id, name, state_name, assignee_ids,
            updated_at, state_updated_at, age_in_state_days, is_stuck,
            target_date}],
  members: [{id, display_name}],
  error
}
```

If a subagent's `error` is populated, skip that project's sections
but keep the run going — a missing project is never a reason to
abort. Collect `subagent_errors: {project_id: error_message}` for
the banner in Step 6.

### Step 4: Build per-person cards (per project)

For each project's response, index `states` by `name` and `members` by
`id` to make lookups cheap.

Fan each issue across every `assignee_id` — a co-assigned issue
counts for every carrier. If an issue has an empty `assignee_ids`
(genuinely unassigned), bucket it under a synthetic `(unassigned)`
person.

For each `person_id` in the resulting fan-out:

- `person` = the display name from the response's `members`
  (`display_name`), or the UUID string if unresolved, or literally
  `(unassigned)` for the synthetic bucket.
- `active_count` = number of the person's issues (all `active` by
  construction).
- `state_breakdown` = `{state_name: count}` from
  `issue.state_name`, e.g.
  `{"In Progress": 3, "Ready for test": 1}`.
- `stuck_items` = the person's issues where `is_stuck == true`,
  each carrying `readable_id` and `age_in_state_days`.
- `last_activity` = max `updated_at` across the person's issues.

### Step 5: Classify tier (per project)

Compute `team_avg` = mean of `active_count` across people in this
project whose count > 0.

Then for each person:
- `attention` — `stuck_items` non-empty.
- `watch` — otherwise, if `active_count > load_multiplier × team_avg`.
- `on_track` — everything else.

### Step 6: Report

If any project returned an error, prepend a banner:

> WARNING: plane-fetcher failed for N project(s): <name>: <error>...
> Standup shipping the projects that succeeded.

Then for each project, print:

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

If a project's response has zero issues after the active filter (or
zero people after fan-out), print `(all clear)` under that project
header and move on.

End the whole report with a totals line across projects:
`TOTALS: people=<n>, active=<n>, stuck=<n>`.

## Guardrails

- **Read-only.** No Plane mutation tools of any kind. If Claude even
  considers one, stop and explain why. (The subagent is also
  read-only; see `.claude/agents/plane-fetcher.md`.)
- **Delegate context-heavy work.** Never call
  `scripts/plane.sh` directly from this skill. The subagent already
  handles it; duplicating those calls in main context defeats the
  purpose of the fan-out.
- If Plane MCP fails mid-run for one project, ship the standup with
  a banner naming what failed. A missing project is never a reason
  to skip the run.
- State classification lives in `plane-fetcher`. If the buckets look
  wrong for a workspace, fix the classifier rules there — not here.
