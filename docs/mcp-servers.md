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
  -- npx -y @gongrzhe/server-gmail-autoauth-mcp
```

On first invocation, the server prints a URL and a device code —
open the URL, paste the code, consent. Token is cached; subsequent
runs are silent.

**Tools the commands rely on:**
- `mcp__gmail__list-emails` (or the server-specific equivalent)
- `mcp__gmail__get-email`
- `mcp__gmail__send-email` — the **only** write path in `/gm`

### gcal — today + 7-day look-ahead

Unlike gmail, `@cocal/google-calendar-mcp` **cannot** run its own OAuth
flow from a fresh install. It requires a Google Cloud OAuth client
credentials file on disk, referenced via the
`GOOGLE_OAUTH_CREDENTIALS` env var. Setup is a three-step dance.

#### 1. Get OAuth client credentials

If you already have `GOOGLE_OAUTH_CLIENT_ID` and
`GOOGLE_OAUTH_CLIENT_SECRET` in `.env` (from the `main` branch's
`app.auth` setup), skip to step 2 — you can synthesize the JSON file
from those values.

Otherwise, get them from Google Cloud Console:

1. https://console.cloud.google.com — new or existing project
2. Enable the **Google Calendar API**
3. **Credentials → Create Credentials → OAuth client ID → Desktop app**
4. Under **OAuth consent screen → Audience**, add your Gmail address
   as a test user
5. Download the JSON

#### 2. Land the credentials file at a stable path

Standard location: `~/.config/gcal-mcp/credentials.json`, mode 600.
The file must be Google's standard Desktop-app OAuth JSON shape:

```json
{
  "installed": {
    "client_id": "…",
    "client_secret": "…",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
    "redirect_uris": ["http://localhost"]
  }
}
```

If you're reusing the id + secret from `.env`, a one-liner writes
this file without ever echoing the values to stdout:

```bash
python3 - <<'PY'
import json, pathlib
env = dict(
    line.strip().split('=', 1)
    for line in pathlib.Path('.env').read_text().splitlines()
    if line.strip() and not line.strip().startswith('#') and '=' in line
)
strip = lambda v: v.strip().strip('"').strip("'")
creds = {'installed': {
    'client_id': strip(env['GOOGLE_OAUTH_CLIENT_ID']),
    'client_secret': strip(env['GOOGLE_OAUTH_CLIENT_SECRET']),
    'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
    'token_uri': 'https://oauth2.googleapis.com/token',
    'auth_provider_x509_cert_url': 'https://www.googleapis.com/oauth2/v1/certs',
    'redirect_uris': ['http://localhost'],
}}
out = pathlib.Path.home() / '.config' / 'gcal-mcp' / 'credentials.json'
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(creds))
out.chmod(0o600)
PY
```

#### 3. Register the MCP and run the auth flow

```bash
claude mcp add --scope user gcal \
  --env GOOGLE_OAUTH_CREDENTIALS="$HOME/.config/gcal-mcp/credentials.json" \
  -- npx -y "@cocal/google-calendar-mcp"

GOOGLE_OAUTH_CREDENTIALS="$HOME/.config/gcal-mcp/credentials.json" \
  npx @cocal/google-calendar-mcp auth
```

The second command opens a browser — consent with the same Gmail
account you used for the gmail MCP. Token is cached; subsequent runs
are silent. `claude mcp list` should then show `gcal ✓ Connected`.

**Gotchas:**
- `--` before `npx` is mandatory — without it, Claude Code's arg
  parser interprets `-y` as its own flag and errors out.
- Quote the package name (`"@cocal/google-calendar-mcp"`) — the arg
  parser can otherwise drop `@`-prefixed args on some CLI versions.
- While the OAuth consent screen is in **Testing** mode, the cached
  token expires every 7 days. Publish the app in Google Cloud Console
  to remove the weekly re-auth.

**Tools relied on:**
- `mcp__gcal__list-events`
- `mcp__gcal__get-current-time` (used in Step 0 of `/gm`)

Calendar is **strictly read-only** in this agent. Every write tool
(`create-event`, `update-event`, `delete-event`, `respond-to-event`)
is denied at both guard layers — `.claude/settings.local.json` and
`GCAL_WRITE_TOOLS` in `app_sdk/run.py`. See CLAUDE.md Part 1.1.

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

If you swap Gmail servers to one where the tool names differ, also
update `GMAIL_WRITE_TOOLS` in `app_sdk/run.py` and the `deny` list in
`.claude/settings.local.json`. Both surfaces block every Gmail
mutation by default; only `/gm` (via the SDK, not `--dry-run`) is
allowed to call `send_email`.

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
