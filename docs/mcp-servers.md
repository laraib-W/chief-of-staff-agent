# MCP Servers — `experiment/claude-agent-sdk`

The `/morning-digest`, `/triage`, and `/plane-standup` commands need three MCP
servers: **gmail**, **gcal**, and **plane**. Install them **once at
user scope** — they'll then be available in every `claude` session on
your machine, including this project and any other. This matches the
[reference repo][ref] design.


## Verify what's already connected

```bash
claude mcp list
```

Servers already on the list can be skipped in the install steps below.

## Install

### Fast path — one script for gmail + gcal + plane

Once you've done the per-server prerequisites below (Cloud Console
setup for Google, `uvx` on PATH for Plane, and
populated `.env` with the five variables from `.env.example`), run:

```bash
./scripts/setup-mcps.sh
```

The script is idempotent — for each server it runs `claude mcp
logout` (clearing any cached OAuth record) and `claude mcp remove`
first, then re-registers via `claude mcp add`. Google auth uses
`MCP_CLIENT_SECRET` env-var handoff so no masked prompt is needed;
Plane's three env vars (`PLANE_API_TOKEN` → `PLANE_API_KEY`,
`PLANE_WORKSPACE_SLUG`, `PLANE_API_HOST_URL` → `PLANE_BASE_URL`) are piped into
the `--env` flags on `claude mcp add`. Read the per-server sections
below for what the prerequisites actually mean before running it the
first time.

Registering is not the same as authenticating. `claude mcp list` will
show gmail and gcal as `! Needs authentication` until you consent once
per server. Either pass `--login` so the script does it right after
registering:

```bash
./scripts/setup-mcps.sh --login          # all three, then consent
./scripts/setup-mcps.sh --login gcal     # just gcal
```

…or run the consent flows yourself afterwards:

```bash
claude mcp login gmail
claude mcp login gcal
```

`--login` needs a terminal — it opens a browser and blocks until
consent returns on the loopback callback, so the script refuses the
flag (rather than hanging) when stdin/stdout isn't a TTY. Plane is
skipped either way: the stdio server authenticates with the API key
from `.env` and has no consent flow.

Each opens a browser tab (add `--no-browser` on a headless box — it
prints the URL and takes the redirect back on stdin). **Do this before
running `app_sdk/run.py`**: the SDK entrypoint spawns a
non-interactive session and cannot perform the consent flow, so it
just surfaces the auth failure.

### gmail — read the last 24h (Google-official HTTP MCP)

We use Google's official Gmail MCP at
`https://gmailmcp.googleapis.com/mcp/v1`. It's thread-based
(`search_threads` / `get_thread`) and does **not** expose any send
capability — perfect fit for this repo, where delivery is
orchestrator-driven from `app_sdk/run.py`.

If you already had the community `@gongrzhe/server-gmail-autoauth-mcp`
installed under the name `gmail`, remove it first:

```bash
claude mcp remove --scope user gmail
```

#### 1. Enable the two Google Cloud APIs

Uses the same Google Cloud project whose OAuth client is in your
`.env` (or wherever you created the Desktop-app client for the send
path):

```bash
gcloud services enable gmail.googleapis.com gmailmcp.googleapis.com
```

Without both, `claude mcp add` will still register the server but the
first tool call fails with a permission error from Google.

#### 2. Create a Web-application OAuth client

Google's Gmail MCP requires a **Web application** OAuth client. **One
client covers everything** — the gmail MCP, the gcal MCP, and the
digest send path in `app_sdk.auth`. Create it once; every step below
reuses it.

1. https://console.cloud.google.com → **APIs & Services → Credentials**
2. **Create Credentials → OAuth client ID → Web application**
3. Pick a stable loopback port (any free port; the docs use `8765`
   below). Under **Authorized redirect URIs**, add **both**:
   ```
   http://localhost:8765/callback
   http://localhost:8766/
   ```
   The first is where Claude Code lands the MCP consent; the second is
   where `app_sdk.auth --setup` lands the send-path consent
   (`google-auth-oauthlib` builds that URI as `http://{host}:{port}/`
   — a bare trailing slash, no path, which is why it differs). Both
   ports are constants: `CALLBACK_PORT` in `scripts/setup-mcps.sh` and
   `SEND_CALLBACK_PORT` in `app_sdk/auth.py`. Change one, change its
   redirect URI to match.
   Exact match. `127.0.0.1` will not work — Claude Code sends
   `localhost`.

   > Google's own MCP setup docs tell you to register
   > `https://claude.ai/api/mcp/auth_callback`. That is the **Claude.ai
   > / Claude Desktop custom-connector** path, not this one. Claude Code
   > and the Agent SDK use a loopback redirect, so they need the
   > `http://localhost:8765/callback` URI above. Registering both on one
   > client is fine if you also use the connector in Claude Desktop.
4. Under **OAuth consent screen → Scopes**, add:
   - `https://www.googleapis.com/auth/gmail.readonly`
   - `https://www.googleapis.com/auth/gmail.compose`
5. Under **OAuth consent screen → Audience**, keep your Gmail address
   listed as a test user.
6. Save the client ID and client secret — the next command asks for
   both.

#### 3. Register the MCP

```bash
claude mcp add --scope user --transport http \
  --client-id <PASTE_CLIENT_ID> \
  --client-secret \
  --callback-port 8765 \
  gmail https://gmailmcp.googleapis.com/mcp/v1
```

- `--client-secret` (no value) prompts once with masked input. To skip
  the prompt (what `setup-mcps.sh` does), keep the flag and pass the
  value through the environment instead:
  `MCP_CLIENT_SECRET='…' claude mcp add …`. Verified on claude CLI
  2.1.236.
- The CLI stores the secret in its own credential store, **not** in
  `~/.claude.json`. A healthy entry there shows only `clientId` and
  `callbackPort` under `oauth`. Absence of `clientSecret` in that file
  is normal and is *not* the cause of an auth failure — don't write one
  in by hand.
- `--callback-port 8765` must match the port you registered in the
  Google Cloud OAuth client. If they differ, consent fails with
  `redirect_uri_mismatch`.

#### 4. Authenticate

```bash
claude mcp login gmail
```

Opens a browser tab for OAuth consent; `--no-browser` prints the URL
instead and takes the redirect back on stdin (for SSH/headless).
Until this succeeds, `claude mcp list` shows the server as
`! Needs authentication` and `app_sdk/run.py` fails at startup — the
SDK entrypoint is non-interactive and cannot run the consent flow.
Once done, the tokens are reused silently by both the CLI and the SDK.

**Delivery stays orchestrator-driven.** `app_sdk/run.py` sends the
morning digest via the Gmail API using a **separate** refresh token
cached in the OS keyring under the service
`chief-of-staff-agent-sdk`. Set that up once with
`python -m app_sdk.auth --setup` (scope: `gmail.send` only).

It uses the **same** Web-application client as the MCP servers — only
the redirect port differs (`8766` vs `8765`). You still consent twice
against the same Gmail account, because the two flows request
different scopes and store their tokens in different places (Claude
Code's credential store for the MCPs, your OS keyring for the send
path). One client, two consents.

**Tools the commands rely on:**
- `mcp__gmail__search_threads`
- `mcp__gmail__get_thread`

Google's server exposes six write tools (`create_draft`,
`create_label`, `label_message`, `label_thread`, `unlabel_message`,
`unlabel_thread`); every one is denied at both guard layers —
`.claude/settings.local.json` and `GMAIL_WRITE_TOOLS` in
`app_sdk/run.py`.

Filter-management tools present on the old community server
(`list_filters`, `get_filter`, `create_filter`) are **not** offered
by Google's official MCP. If the `/triage` command ever needs
filter-aware tiering, we'd have to add it via a separate integration.

### gcal — today + 7-day look-ahead (Google-official HTTP MCP)

We use Google's official Calendar MCP at
`https://calendarmcp.googleapis.com/mcp/v1`. It's in Developer Preview
(not GA) — same posture as the Gmail MCP above — and mirrors the
Gmail install pattern: HTTP transport, Web-application OAuth client.

If you already had the community `@cocal/google-calendar-mcp` server
installed under the name `gcal`, remove it first:

```bash
claude mcp remove --scope user gcal
```

#### 1. Enable the two Google Cloud APIs

Same Google Cloud project as gmail:

```bash
gcloud services enable calendar-json.googleapis.com calendarmcp.googleapis.com
```

Without both, `claude mcp add` will still register the server but the
first tool call fails with a permission error from Google.

#### 2. Reuse or create a Web-application OAuth client

You can reuse the Web-app client from the gmail step above — just add
the three Calendar scopes to its **OAuth consent screen → Scopes**:

- `https://www.googleapis.com/auth/calendar.calendarlist.readonly`
- `https://www.googleapis.com/auth/calendar.events.freebusy`
- `https://www.googleapis.com/auth/calendar.events.readonly`

The `http://localhost:8765/callback` redirect URI already registered
on that client works for gcal too. If you'd rather isolate credentials
per MCP, create a fresh Web-application client following steps 1–5 of
the gmail section with the Calendar scopes above (and register a
different callback port to avoid overlap).

#### 3. Register the MCP

```bash
claude mcp add --scope user --transport http \
  --client-id <PASTE_CLIENT_ID> \
  --client-secret \
  --callback-port 8765 \
  gcal https://calendarmcp.googleapis.com/mcp/v1
```

- `--client-secret` (no value) prompts once with masked input;
  `MCP_CLIENT_SECRET='…' claude mcp add …` supplies it without the
  prompt. As with gmail, the secret does not appear in
  `~/.claude.json` — that's expected.
- `--callback-port 8765` must match a port registered on the OAuth
  client's authorized redirect URIs.

#### 4. Authenticate

```bash
claude mcp login gcal
```

Same flow as gmail above — one browser consent, then silent. Required
before `app_sdk/run.py` can use the server.

**Tools relied on:**
- `mcp__gcal__list_events` (used by the `calendar-fetcher` subagent)

Google's server exposes four write tools (`create_event`,
`update_event`, `delete_event`, `respond_to_event`); every one is
denied at both guard layers — `.claude/settings.local.json` and
`GCAL_WRITE_TOOLS` in `app_sdk/run.py`. See CLAUDE.md Part 1.1.

Step 0 of `/morning-digest` (grounding the clock) no longer uses a
calendar tool — the official MCP does not expose `get-current-time`.
It now shells out via `Bash(date +'%Y-%m-%d %A %Z')`, which is why
`.claude/settings.local.json` allows `Bash(date *)`.

### plane — issues, states, assignees per project

Already installed? If `claude mcp list` shows a `plane` (or similarly
named) server connected, skip this section — the slash commands use
whatever's connected under that name.

#### Install

```bash
claude mcp add --scope user plane \
  --env PLANE_API_KEY=<paste-here> \
  --env PLANE_WORKSPACE_SLUG=<your-slug> \
  --env PLANE_BASE_URL=<your-plane-host-url> \
  -- uvx plane-mcp-server stdio
```

Prereq: `uvx` on your `PATH` (ships with `uv`). It fetches
`plane-mcp-server` from PyPI on demand — no install step.

The npm `@makeplane/plane-mcp-server` is a different, unmaintained
implementation (0.1.5, TypeScript, ~47 flat tools). v0.3.x is Python,
30 action-dispatch tools, and is the one these skills target.

#### Filling the three env vars

| Var | What | Example (Arbisoft) |
|---|---|---|
| `PLANE_API_KEY` | Personal API token — Plane → **Workspace Settings → API tokens** → create → copy | `plane_api_…` |
| `PLANE_WORKSPACE_SLUG` | The short name in your Plane URL: `<host>/<slug>/` | `arbisoft` |
| `PLANE_BASE_URL` | Base URL of your Plane instance (cloud or self-hosted). Named `PLANE_API_HOST_URL` in `.env`; `setup-mcps.sh` maps it. | `https://projects.arbisoft.com` |

#### What it produces

The command above writes this entry into `~/.claude.json` under
`mcpServers`:

```json
"plane": {
  "type": "stdio",
  "command": "uvx",
  "args": ["plane-mcp-server", "stdio"],
  "env": {
    "PLANE_API_KEY": "…",
    "PLANE_WORKSPACE_SLUG": "arbisoft",
    "PLANE_BASE_URL": "https://projects.arbisoft.com"
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

Authenticate once with `claude mcp login plane` before the first run
(same reasoning as gmail/gcal — the SDK entrypoint can't consent).
Only available for cloud Plane accounts — self-hosted instances (like Arbisoft's
`projects.arbisoft.com`) must use the stdio path above.

**Tools the commands rely on:**
- `mcp__plane__workitem`  (`action: "list"`)
- `mcp__plane__state`     (`action: "list"`, fallback only)
- `mcp__plane__member`    (`action: "list_project"`)

Tool namespace depends on the name you gave the server in
`claude mcp add`. If you used something other than `plane`, the tool
prefix changes accordingly — Claude figures this out from
`claude mcp list`, no config change needed on our side.

## Package / server caveats

- **Gmail** — verified: `https://gmailmcp.googleapis.com/mcp/v1`
  (Google-official HTTP MCP). Requires both `gmail.googleapis.com`
  and `gmailmcp.googleapis.com` enabled on the Google Cloud project
  behind the Web-app OAuth client.
- **Plane cloud** — verified: `https://mcp.plane.so/http/mcp` (OAuth).
- **Plane self-hosted** — verified: `uvx plane-mcp-server` (PyPI).
- **Calendar** — verified: `https://calendarmcp.googleapis.com/mcp/v1`
  (Google-official HTTP MCP, Developer Preview). Requires both
  `calendar-json.googleapis.com` and `calendarmcp.googleapis.com`
  enabled on the Google Cloud project behind the Web-app OAuth client.
  Being in Developer Preview means the tool surface and endpoint could
  change before GA — if a tool call ever fails with an unexpected
  schema error, check the endpoint against
  [developers.google.com/workspace/calendar/api/guides/configure-mcp-server](https://developers.google.com/workspace/calendar/api/guides/configure-mcp-server).

If you ever swap Gmail servers to one where the tool names differ,
update the six entries in `GMAIL_WRITE_TOOLS` (`app_sdk/run.py`) and
in the `deny` list of `.claude/settings.local.json`. Both surfaces
block every Gmail mutation unconditionally — no tool exception for
`/morning-digest`. Delivery goes through the Gmail API in `run.py`,
not through the MCP.

## Troubleshooting

### `Couldn't complete authentication for "gmail": client_secret is missing.`

That message comes from Google's token endpoint, not from Claude Code
— the browser consent succeeded (so your client ID and the
`http://localhost:8765/callback` redirect URI are both fine), but the
follow-up code→token exchange went out without a client secret.

Two causes, in order of likelihood:

1. **A stale cached OAuth record.** `claude mcp login` reuses a
   per-server OAuth record stored outside `~/.claude.json`. If the
   server was first registered without a secret, that record persists
   across a plain `claude mcp remove` + `add`. Clear it explicitly:

   ```bash
   claude mcp logout gmail
   claude mcp login gmail
   ```

2. **The secret never landed.** Note that you cannot check this by
   looking for `clientSecret` in `~/.claude.json` — the CLI keeps it
   elsewhere, and a working server has only `clientId` and
   `callbackPort` there. The real test is whether `claude mcp login`
   completes. If it doesn't, re-register with the env-var handoff
   (this is what `setup-mcps.sh` does, and it also runs
   `claude mcp logout` first):

   ```bash
   claude mcp remove --scope user gmail
   MCP_CLIENT_SECRET='<secret>' claude mcp add --scope user --transport http \
     --client-id '<client id>' --client-secret --callback-port 8765 \
     gmail https://gmailmcp.googleapis.com/mcp/v1
   claude mcp login gmail
   ```

### `getaddrinfo ENOTFOUND gmailmcp.googleapis.com`

DNS, not config. Both `gmailmcp.googleapis.com` and
`calendarmcp.googleapis.com` are live (they answer `200` on
`POST /mcp/v1`, and OAuth metadata is served at
`/.well-known/oauth-protected-resource/mcp/v1`). Check VPN / resolver
state and retry:

```bash
dig +short gmailmcp.googleapis.com
```

### `! Needs authentication` in `claude mcp list`

Registration succeeded, consent hasn't happened. Run
`claude mcp login <name>` — see step 4 of each section above.

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

If you're using cloud Plane via hosted OAuth, run
`claude mcp login plane` first — subsequent calls are silent. The
stdio `plane-mcp-server` path needs no consent; it authenticates with
the API key from `.env`.

**Full `/morning-digest` (needs Gmail + Calendar OAuth done first,
plus `python -m app_sdk.auth --setup` for the send path):**

```bash
python -m app_sdk.run --dry-run    # prints digest, skips send
python -m app_sdk.run              # delivers via the Gmail API
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
