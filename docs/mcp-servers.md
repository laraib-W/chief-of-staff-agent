# MCP Servers — `experiment/claude-agent-sdk`

The `/gm`, `/triage`, and `/plane-standup` commands need three MCP
servers: **gmail**, **gcal**, and **plane**. Install them **once at
user scope** — they'll then be available in every `claude` session on
your machine, including this project and any other. This matches the
[reference repo][ref] design.

There is **no `.mcp.json`** in this repo. Project-scope MCP is
possible with Claude Code, but user-scope keeps setup out of the
project directory and lets a single OAuth flow serve any Claude
session.

[ref]: https://github.com/mimurchison/claude-chief-of-staff

## Verify what's already connected

```bash
claude mcp list
```

Servers already on the list can be skipped in the install steps below.

## Install

### gmail — read the last 24h, deliver the digest

```bash
claude mcp add --scope user gmail \
  npx -y @gongrzhe/server-gmail-autoauth-mcp
```

On first invocation, the server prints a URL and a device code —
open the URL, paste the code, consent. Token is cached; subsequent
runs are silent.

**Tools the commands rely on:**
- `mcp__gmail__list-emails` (or the server-specific equivalent)
- `mcp__gmail__get-email`
- `mcp__gmail__send-email` — the **only** write path in `/gm`

### gcal — today + 7-day look-ahead

```bash
claude mcp add --scope user gcal \
  npx -y @cocal/google-calendar-mcp
```

Same OAuth pattern as gmail. If you already consented for the same
Google account when installing gmail, some MCPs reuse the token — but
most calendar MCPs run their own flow. Follow the prompts.

**Tools relied on:**
- `mcp__gcal__list-events`
- `mcp__gcal__get-current-time` (used in Step 0 of `/gm`)

### plane — issues, states, assignees per project

Already installed? If `claude mcp list` shows a `plane` (or similarly
named) server connected, skip this section — the slash commands use
whatever's connected under that name.

#### Install

```bash
claude mcp add --scope user plane \
  --env PLANE_API_KEY=<paste-here> \
  --env PLANE_WORKSPACE_SLUG=<your-slug> \
  --env PLANE_API_HOST_URL=<your-plane-host-url> \
  -- plane-mcp-server
```

Prereq: `plane-mcp-server` on your `PATH`. Install once with:

```bash
pipx install plane-mcp-server        # recommended
# or
pip install plane-mcp-server
```

(If you'd rather skip the install, swap `-- plane-mcp-server` for
`-- uvx plane-mcp-server stdio` — `uvx` fetches on demand.)

#### Filling the three env vars

| Var | What | Example (Arbisoft) |
|---|---|---|
| `PLANE_API_KEY` | Personal API token — Plane → **Workspace Settings → API tokens** → create → copy | `plane_api_…` |
| `PLANE_WORKSPACE_SLUG` | The short name in your Plane URL: `<host>/<slug>/` | `arbisoft` |
| `PLANE_API_HOST_URL` | Base URL of your Plane instance (cloud or self-hosted) | `https://projects.arbisoft.com` |

#### What it produces

The command above writes this entry into `~/.claude.json` under
`mcpServers`:

```json
"plane": {
  "type": "stdio",
  "command": "plane-mcp-server",
  "args": [],
  "env": {
    "PLANE_API_KEY": "…",
    "PLANE_WORKSPACE_SLUG": "arbisoft",
    "PLANE_API_HOST_URL": "https://projects.arbisoft.com"
  }
}
```

Verify with `claude mcp list` — the entry should show as connected.

**Note on the key:** Claude Code stores env values in `~/.claude.json`
in cleartext. Rotate the API token at Plane if you ever share that
file. There is no `.env` involvement — the key is pasted **once** at
install time.

#### If you're on cloud Plane (`app.plane.so`)

Plane also hosts a remote MCP with OAuth (no API key needed). Skip
the install above and instead:

```bash
claude mcp add --scope user --transport http plane https://mcp.plane.so/http/mcp
```

First tool call opens a browser for consent. Only available for cloud
Plane accounts — self-hosted instances (like Arbisoft's
`projects.arbisoft.com`) must use the stdio path above.

**Tools the commands rely on:**
- `mcp__plane__list_project_issues`
- `mcp__plane__list_states`
- `mcp__plane__get_workspace_members`

Tool namespace depends on the name you gave the server in
`claude mcp add`. If you used something other than `plane`, the tool
prefix changes accordingly — Claude figures this out from
`claude mcp list`, no config change needed on our side.

## Package / server caveats

- **Plane cloud** — verified: `https://mcp.plane.so/http/mcp` (OAuth).
- **Plane self-hosted** — verified: `uvx plane-mcp-server` (PyPI).
- **Gmail and Calendar** — the npm packages listed above are common
  community servers but **have not been re-verified against npm's
  current registry**. If either `claude mcp add …` fails with
  "package not found," run `npm view <package>` and find the current
  server on [modelcontextprotocol.io/servers](https://modelcontextprotocol.io/servers).

If you swap Gmail servers to one where the send tool has a different
name, also update `DRY_RUN_DENIED_TOOLS` in `app_sdk/run.py`.

## Smoke test

**Plane-only (fastest):**

```bash
# 1. Fill goals.yaml → plane.workspace_slug, plane.project_ids
# 2. Interactive
claude
> /plane-standup

# 3. Or via SDK
python -m app_sdk.run --command /plane-standup --dry-run
```

If you're using cloud Plane via hosted OAuth, the very first Plane
tool call opens a browser for consent — subsequent calls are silent.

**Full `/gm` (needs Gmail + Calendar OAuth done first):**

```bash
python -m app_sdk.run --dry-run    # prints digest, denies send-email
python -m app_sdk.run              # delivers via gmail MCP
```

## Uninstall

```bash
claude mcp remove --scope user gmail
claude mcp remove --scope user gcal
claude mcp remove --scope user plane
```

## OAuth already done on `main`?

If you completed `python -m app.auth --setup` on `main`, that token
lives in your OS keyring under a key the MCP servers do not read.
The MCP servers each run their own OAuth flow the first time. This
is the price of running two independent implementations side by side
— acceptable for an experiment; addressable if the SDK path wins and
we consolidate.
