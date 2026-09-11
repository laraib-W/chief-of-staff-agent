# SDK / Claude Code Setup — `experiment/claude-agent-sdk`

This branch adds an alternative implementation of the morning digest,
built as a **Claude Code project** (CLAUDE.md + slash commands + MCP
servers) with a Python entrypoint via `claude-agent-sdk`. The
LangGraph pipeline on `main` is untouched — the two live side by side.

## What's on this branch that isn't on main

```
CLAUDE.md              # persona + non-negotiables (read-only, cited)
.claude/commands/morning-digest.md  # morning digest slash command
.claude/commands/triage.md          # read-only inbox tiering
.claude/commands/plane-standup.md
.claude/commands/plane-setup.md
goals.yaml             # shared defaults: thresholds, trusted domains, objectives
goals.local.yaml       # gitignored: identity + Plane projects (merged over the above)
app_sdk/__init__.py
app_sdk/run.py         # `python -m app_sdk.run` — cron/launchd entry
docs/sdk-setup.md      # this file
docs/mcp-servers.md    # per-server install notes
```

MCP servers are **not** wired via a project-scope `.mcp.json`. They are
installed once at user scope with `claude mcp add --scope user …` and
apply to every `claude` session on the machine (this matches the
reference repo's design). See `docs/mcp-servers.md` for install
commands.

## Two ways to run the digest

### Interactive (Claude Code CLI)

```bash
# One-time
brew install --cask claude-code       # or npm i -g @anthropic-ai/claude-code
cd /path/to/chief-of-staff-agent
git checkout experiment/claude-agent-sdk

# Every morning
claude
# then type: /morning-digest
```

Claude Code auto-loads `CLAUDE.md` and every file in `.claude/commands/`
from the repo root. MCP servers come from your user-scope Claude
config (see [`mcp-servers.md`](mcp-servers.md)) — not from a project
`.mcp.json`. The `/morning-digest` slash command follows `.claude/commands/morning-digest.md`
step-by-step.

### Scheduled (Python SDK)

```bash
uv sync                                        # installs claude-agent-sdk
python -m app_sdk.auth --setup                 # one-time: authorize send path
python -m app_sdk.run --dry-run                # prints digest, no send
python -m app_sdk.run                          # delivers via Gmail API
python -m app_sdk.run --to you@example.com     # override recipient
python -m app_sdk.run --command /triage        # any slash command
```

`app_sdk/run.py` invokes the SDK with `cwd=<repo root>` so the same
CLAUDE.md + slash commands + MCP servers are picked up. Point cron or
launchd at `python -m app_sdk.run` to get a daily delivery.

**macOS shortcut** — a LaunchAgent template is checked in:

```bash
./scripts/install-schedule.sh              # default: 08:00 local time
HOUR=7 MINUTE=30 ./scripts/install-schedule.sh
```

The script resolves the repo root and `uv` path, substitutes into
`scripts/chief-of-staff-agent-sdk.plist.template`, writes the resulting
plist to `~/Library/LaunchAgents/com.chief-of-staff-agent-sdk.plist`,
and reloads it (`launchctl bootout` + `bootstrap`). Idempotent —
re-run any time to change the hour. First scheduled run must be
preceded by an interactive `python -m app_sdk.auth --setup` and by
`claude mcp login gmail` + `claude mcp login gcal`, so the MCP OAuth
tokens are cached. A scheduled run is non-interactive and cannot
perform either consent flow itself.

Delivery does **not** go through the Gmail MCP server. The agent
emits the finished HTML between `<<<DIGEST-START>>>` /
`<<<DIGEST-END>>>` markers; `run.py` extracts the block and sends it
via the Gmail API using credentials from `app_sdk.auth` (keyring
service `chief-of-staff-agent-sdk`, scope `gmail.send` only). Every
Gmail write tool is denied to the agent — the send path is
orchestrator-owned and deterministic.

## Prerequisites

1. **`jq` on `PATH`** — a hard dependency, not a convenience.
   `.claude/agents/plane-fetcher.md` shells out to `jq` to filter
   Plane's oversized MCP payloads on disk instead of reading them
   into context (a 492-issue project returns ~205KB even with
   `fields` narrowing, and the member list ~15KB). Install with `brew install jq` /
   `apt install jq`. Without it, member resolution fails and the
   digest prints raw UUIDs instead of names.
2. **A logged-in Claude Code CLI** — run `claude` once and `/login`
   if you have not. The SDK entrypoint spawns the CLI, which uses that
   session; no `ANTHROPIC_API_KEY` is needed. Set one only if you
   deliberately want API-console billing instead of your subscription.
3. **Google OAuth client** — same Desktop-app OAuth client used by
   `app/` on main (`GOOGLE_OAUTH_CLIENT_ID` / `_SECRET` in `.env`).
   See [`google-oauth-setup.md`](google-oauth-setup.md).
4. **Send-path token** — run `python -m app_sdk.auth --setup` once
   to authorize the `gmail.send` scope and cache the refresh token in
   your OS keyring. Independent of the Gmail MCP's own OAuth.
5. **Plane API token** — same one used by `app/`. `PLANE_API_TOKEN` in
   `.env`, plus `PLANE_WORKSPACE_SLUG`.
6. **MCP servers installed *and authenticated*** — see
   [`mcp-servers.md`](mcp-servers.md). Registering with
   `claude mcp add` is not enough: run `claude mcp login gmail` and
   `claude mcp login gcal` once each, and confirm `claude mcp list`
   shows both as `✔ Connected` rather than
   `! Needs authentication`.
7. **`goals.yaml` filled in** — replace every `TODO` at repo root.
   `identity.delivery_address` in `goals.local.yaml` is what `run.py`
   sends the digest to.
8. **Permission rules** — nothing to do; they ship in
   `.claude/settings.json`, which is committed precisely so a fresh
   clone works. It allows the read-only MCP tools plus the `jq` /
   calls `plane-fetcher` needs, and denies every
   Gmail, Calendar, and Plane mutation. `.claude/settings.local.json`
   stays gitignored for per-machine overrides. If you add a rule
   everyone needs, put it in `settings.json`.

## Design notes

- **Same runtime, two surfaces.** The Claude Code CLI and the
  `claude-agent-sdk` Python package invoke the same underlying agent
  (Claude + MCP + tools). The CLI is interactive; the SDK is
  programmatic. Same `CLAUDE.md` and `.claude/commands/` apply to both; MCPs
  come from your user-scope Claude config.
- **No shared code with `app/`.** This branch does not import from
  `app/*`. The two implementations are fully independent so we can
  compare architectures fairly. If the SDK version proves out, we
  discuss what to consolidate.
- **Deliberately no persistence.** No `runs.db`, no `memory.db`, no
  digest archive. If the experiment survives past the first week, we
  add these — but not before we know they're needed.
- **Read-only preserved.** Same constraint as main: only allowed write
  is delivering the digest. See `CLAUDE.md` Part 1.1.

## Comparing to main

| Aspect                | `main` (LangGraph)                   | `experiment/claude-agent-sdk`         |
|-----------------------|--------------------------------------|----------------------------------------|
| Runtime               | Python + LangGraph StateGraph        | Claude Code / SDK + MCP                |
| Pipeline shape        | Fixed 8-node fan-out/fan-in          | Claude decides tool order              |
| Providers             | `app/providers/*.py` (custom)        | Community MCP servers                  |
| Judgment              | `providers/llm.py` per node          | Emergent — one long agent turn         |
| Rendering             | Jinja template in `app/templates/`   | Claude writes HTML directly            |
| Persistence           | SQLite (runs, memory, checkpoints)   | None yet                               |
| Scheduling            | `python -m app.run` via cron         | `python -m app_sdk.run` via cron       |
| Determinism           | High                                 | Lower — LLM chooses tool order         |
| LOC to maintain       | ~2000                                | ~150 (markdown + 100 lines Python)     |

## Troubleshooting

- **`claude-agent-sdk` import error** — `uv sync` on this branch to
  pull the new dep.
- **MCP server not connecting** — run `claude mcp list` to see which
  servers loaded and which errored. If a server is missing entirely,
  re-run the `claude mcp add --scope user …` command from
  `docs/mcp-servers.md`. If it's connected but tool calls fail, check
  `.env` has every variable the server needs.
- **`! Needs authentication`, or an auth error at SDK startup** — the
  server is registered but never consented. Run
  `claude mcp login <name>`; the SDK entrypoint is non-interactive and
  cannot open the consent flow. If consent succeeds but the exchange
  fails with `client_secret is missing`, see the troubleshooting
  section in [`mcp-servers.md`](mcp-servers.md).
- **Digest ran but no email arrived** — delivery is done by `run.py`,
  not the agent. Confirm `python -m app_sdk.auth --setup` was run and
  the keyring entry exists (`chief-of-staff-agent-sdk` /
  `google_oauth_refresh_token`). Then confirm the agent actually
  emitted the digest block — grep the transcript for `<<<DIGEST-START>>>`.
  If missing, `/morning-digest` produced text without the markers and
  `run.py` bailed out.
- **`No Google refresh token in keyring`** — you skipped
  `python -m app_sdk.auth --setup`. Run it once; the flow opens a
  browser for consent.
- **Plane fetch is slow and burns tokens** — `plane-fetcher`'s `jq`
  calls are being denied, or `jq` is missing. Confirm `jq --version`
  works, then confirm `.claude/settings.json` is present and allows
  `Bash(jq *)`. This degrades quietly: the fetcher falls back to
  reading the whole ~205KB payload through context, so the run still
  succeeds, just expensively.
- **A Plane call was blocked with "is not a read action"** — correct
  behavior. `scripts/plane-readonly-guard.sh` is a `PreToolUse` hook
  that default-denies any Plane `action` outside the read allowlist,
  because v0.3.x serves reads and writes through one tool per resource
  and a name-based deny rule cannot separate them.
- **A Plane call was blocked for passing `pql`** — also correct. See
  "Why filtering is done client-side" in
  [`mcp-servers.md`](mcp-servers.md).
- **Agent tried to call a Gmail write tool (`create_draft`,
  `label_*`, `unlabel_*`, `create_label`) and was denied** — correct
  behavior. Google's official Gmail MCP does not offer a `send_email`
  tool at all; the six writes it does offer are all on the deny list.
  Delivery is orchestrator-driven from `run.py`. If your slash
  command is telling Claude to call any of these, update the command
  markdown.
