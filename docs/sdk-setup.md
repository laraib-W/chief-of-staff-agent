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
goals.yaml             # identity, thresholds, Plane project IDs
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
python -m app_sdk.run --dry-run                # prints digest, no send
python -m app_sdk.run                          # delivers via Gmail MCP
python -m app_sdk.run --command /triage        # any slash command
```

`app_sdk/run.py` invokes the SDK with `cwd=<repo root>` so the same
CLAUDE.md + slash commands + MCP servers are picked up. Point cron or
launchd at `python -m app_sdk.run` to get a daily delivery.

## Prerequisites

1. **Anthropic API key** — set `ANTHROPIC_API_KEY` in `.env`.
2. **Google OAuth client** — same one used by `app/` on main. See
   [`google-oauth-setup.md`](google-oauth-setup.md).
3. **Plane API token** — same one used by `app/`. `PLANE_API_TOKEN` in
   `.env`, plus `PLANE_WORKSPACE_SLUG`.
4. **MCP servers installed** — see [`mcp-servers.md`](mcp-servers.md).
5. **`goals.yaml` filled in** — replace every `TODO` at repo root.

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
- **`/morning-digest` calls send_email when it shouldn't** — check
  `GMAIL_WRITE_TOOLS` in `app_sdk/run.py` matches your Gmail MCP
  server's actual tool names (see the server's docs). The default
  list matches `@gongrzhe/server-gmail-autoauth-mcp` v1.1.11. That
  file's `_disallowed_tools` allows `send_email` only for
  `--command /morning-digest` in non-dry-run; everything else is denied.
