# Morning Chief-of-Staff Agent

A single-user agent that runs once each morning, reads Gmail, Google Calendar,
and Plane, and delivers one prioritized HTML digest by email — so the first
thirty minutes of the day are spent deciding, not triaging.

## What it does

Every morning at a configured time (default 08:00 local), the agent:

1. Fetches the last 24h of email, today's calendar with a 7-day look-ahead,
   and the current state of configured Plane projects — all in parallel.
2. Applies deterministic rules (allowlists, thresholds, overdue grace) and
   LLM judgment (classify email intent, summarize team state, rank the day's
   top priorities).
3. Correlates signals across sources (e.g. an email ask tied to a stalled
   Plane issue).
4. Emails a single digest to the user, with cited evidence for every claim.

The agent is strictly **read-only** against every external system. It never
replies, comments, or writes back.

## Design principles

- **Fixed pipeline, not a ReAct loop.** Eight nodes, four stages, deterministic
  topology.
- **Deterministic rules + LLM judgment, cleanly separated.** Rules decide what
  is overdue; the LLM decides what deserves attention.
- **Every claim is cited.** Issue IDs, quoted asks, timestamps.
- **Replay mode is first-class.** Real API responses are recorded to fixtures
  and replayed offline for prompt tuning and regression tests.
- **Graceful degradation.** The digest always ships, even if a sensor is down.

## Quickstart

> Setup instructions will land alongside the Phase 0 scaffold. Placeholder:

```bash
# install
uv sync

# configure
cp config.example.yaml config.yaml
cp .env.example .env
# fill in Gmail/Calendar OAuth client and Plane API token

# run
python -m app.run                    # normal morning run
python -m app.run --replay           # replay from fixtures
python -m app.run --dry-run          # skip email delivery
```

## Repository layout

| File               | Purpose                                                  |
|--------------------|----------------------------------------------------------|
| [specs.md](specs.md)             | Technical specification — pipeline, schemas, config, LLM budget. Source of truth for behavior. |
| [EVALUATION.md](EVALUATION.md)   | Success metrics, exit criteria, golden datasets.         |
| [SECURITY.md](SECURITY.md)       | Threat model, scopes, credential handling.               |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Branching, PR checklist, testing conventions.          |

## Status

Pre-release. See `specs.md` for the build roadmap and `EVALUATION.md` for
the exit criteria that gate each stage.
