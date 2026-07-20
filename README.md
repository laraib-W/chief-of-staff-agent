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

## How you use it

- **Daily interface:** the morning email. That's it — you open your inbox,
  the digest is there.
- **Setup and debug interface:** the CLI (`python -m app.run`, `--replay`,
  `--dry-run`, `--config`). Used during install and tuning, not part of the
  daily loop.

The agent is a **scheduled batch job**, not a service. You (or your machine's
scheduler) invoke it once each morning; it runs in under 90 seconds and
exits.

## Quickstart

> Precise commands land with the Phase 0 scaffold. The shape below is the
> intended flow.

### 1. Install

```bash
uv sync
```

### 2. Provision credentials

```bash
cp .env.example .env
# fill in:
#   PLANE_API_TOKEN          - from Plane → Settings → API Tokens
#   ANTHROPIC_API_KEY        - from https://console.anthropic.com
#   GOOGLE_OAUTH_CLIENT_ID   - from Google Cloud Console → OAuth client
#   GOOGLE_OAUTH_CLIENT_SECRET
```

Full credential inventory and rotation rules in [SECURITY.md](SECURITY.md) §2.

### 3. Complete the Google OAuth flow (once)

```bash
python -m app.auth --setup
```

Opens a browser, you consent, the refresh token is stored in the OS keyring.
Never touches disk.

> **Note:** The OAuth client in Google Cloud Console must be of type **Desktop app**
> so the local-server redirect works.

To rotate the token later (e.g. after revoking access):

```bash
python -m app.auth --reauth
```

### 4. Configure

```bash
cp config.example.yaml config.yaml
# edit identity.delivery_address, timezone, run_time,
# plane.project_ids, gmail.trusted_domains, thresholds
```

Every key is documented in [specs.md](specs.md) §6.3.

### 5. Smoke test

```bash
python -m app.run --dry-run    # runs the full pipeline, skips email delivery
python -m app.run --replay     # runs against recorded fixtures, offline
```

### 6. Schedule the daily run

Point your OS scheduler at `python -m app.run` at your configured `run_time`.

**macOS** — a `launchd` `.plist` at `~/Library/LaunchAgents/`:

```xml
<key>ProgramArguments</key>
<array>
    <string>/path/to/uv</string>
    <string>run</string>
    <string>python</string>
    <string>-m</string>
    <string>app.run</string>
</array>
<key>StartCalendarInterval</key>
<dict>
    <key>Hour</key><integer>8</integer>
    <key>Minute</key><integer>0</integer>
</dict>
```

**Linux** — a crontab entry:

```
0 8 * * * cd /path/to/chief-of-staff-agent && /path/to/uv run python -m app.run
```

**Windows** — Task Scheduler, daily trigger at your `run_time`, action
`python -m app.run`.

## Repository layout

| File / directory   | Purpose                                                  |
|--------------------|----------------------------------------------------------|
| [specs.md](specs.md)               | Technical specification — pipeline, schemas, config, LLM budget. Source of truth for behavior. |
| [EVALUATION.md](EVALUATION.md)     | Success metrics, exit criteria, golden datasets.         |
| [SECURITY.md](SECURITY.md)         | Threat model, scopes, credential handling.               |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Branching, PR checklist, testing conventions.            |
| [docs/roadmap.md](docs/roadmap.md) | Build phases 0–4 with deliverables and exit criteria.    |
## Status

![CI](https://github.com/laraib-W/chief-of-staff-agent/actions/workflows/ci.yml/badge.svg)

Pre-release — Phase 0 scaffold in progress. See [docs/roadmap.md](docs/roadmap.md)
for the build phases and [EVALUATION.md](EVALUATION.md) for the exit criteria
that gate each one.
