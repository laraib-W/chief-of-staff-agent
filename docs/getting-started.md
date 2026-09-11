# Getting Started — from zero to a working morning digest

One file, every step, in order. Follow it top to bottom and you will
have the agent reading your Gmail, Calendar, and Plane, and emailing
you a prioritized digest each morning.

**Time:** ~30–45 minutes the first time, most of it in the Google Cloud
Console.

**Branch:** this guide covers `experiment/claude-agent-sdk` — the
Claude Code / MCP implementation in `app_sdk/`. It is not the
LangGraph pipeline in `app/` on `main`; the README's Quickstart
describes that one.

**What you are wiring up:**

| Piece | Why |
|---|---|
| Plane personal access token | read issues, states, assignees |
| Google Cloud OAuth client | one client serves all three consumers below |
| `gmail` + `gcal` MCP servers | Claude reads the last 24h of mail and your calendar |
| `plane` MCP server | Claude reads your projects |
| Send-path OAuth token | `run.py` emails you the finished digest |

Everything is **read-only** except the one digest email. See
`CLAUDE.md` Part 1.

---

## Step 1 — Install the tools

| Tool | Check | Install |
|---|---|---|
| Python ≥ 3.11 | `python3 --version` | python.org or your package manager |
| `uv` | `uv --version` | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Claude Code CLI | `claude --version` | `npm i -g @anthropic-ai/claude-code` |
| `jq` | `jq --version` | `brew install jq` / `apt install jq` |
| `pipx` | `pipx --version` | `brew install pipx` / `python3 -m pip install --user pipx` |

`jq` is **not optional**. `.claude/agents/plane-fetcher.md` shells out
to it to filter Plane's oversized MCP payloads on disk. Without it the
digest prints raw UUIDs instead of teammates' names.

## Step 2 — Clone and install dependencies

```bash
git clone <repo-url> chief-of-staff-agent
cd chief-of-staff-agent
git checkout experiment/claude-agent-sdk
uv sync
```

## Step 3 — Get your Plane credentials

You need three values.

**3a. Personal access token** — in Plane, click your avatar, then:

> **Profile → Settings → Personal Access Tokens → Add personal access
> token**

Name it something like `chief-of-staff-agent`, create it, and copy the
value immediately — Plane shows it once. It looks like `plane_api_…`.

> Some Plane versions expose this as **Workspace Settings → API
> Tokens** instead. Either token works; the personal one is scoped to
> you, which is what you want here.

**3b. Workspace slug** — the short name in your Plane URL. For
`https://projects.example.com/acme/projects/…` the slug is `acme`.

**3c. API host URL** — the base URL of your Plane instance:

- Plane Cloud: `https://api.plane.so`
- Self-hosted: e.g. `https://projects.example.com`

## Step 4 — Set up the Google Cloud project

**One** OAuth client serves the gmail MCP, the gcal MCP, and the send
path. Create it once.

**4a. Create or pick a project** at
<https://console.cloud.google.com> (top-left project picker → **New
Project**).

**4b. Enable four APIs.** With the `gcloud` CLI:

```bash
gcloud services enable \
  gmail.googleapis.com gmailmcp.googleapis.com \
  calendar-json.googleapis.com calendarmcp.googleapis.com
```

Or in the Console: **APIs & Services → Library**, search each of the
four by name, click **Enable**. Miss one and the server registers fine
but the first tool call fails with a permission error.

**4c. Create the OAuth client.** **APIs & Services → Credentials →
Create Credentials → OAuth client ID → Web application.**

It must be **Web application**, not Desktop — Google's Gmail MCP
requires it.

Under **Authorized redirect URIs** add **both**, exactly:

```
http://localhost:8765/callback
http://localhost:8766/
```

The first is where Claude Code lands MCP consent; the second is where
`app_sdk.auth --setup` lands send-path consent. The trailing slash and
missing path on `8766` are not a typo — `google-auth-oauthlib` builds
the URI as `http://{host}:{port}/`. Use `localhost`, never
`127.0.0.1`; Claude Code sends `localhost` and Google matches
literally.

> Google's own MCP docs tell you to register
> `https://claude.ai/api/mcp/auth_callback`. That is for Claude
> Desktop connectors, not Claude Code. You need the loopback URIs
> above.

**4d. Add the scopes.** **OAuth consent screen → Data Access → Add or
remove scopes.** Every scope any consumer requests must be listed, or
consent fails:

```
https://www.googleapis.com/auth/gmail.readonly              (gmail MCP)
https://www.googleapis.com/auth/gmail.compose               (gmail MCP)
https://www.googleapis.com/auth/gmail.send                  (send path)
https://www.googleapis.com/auth/calendar.calendarlist.readonly
https://www.googleapis.com/auth/calendar.events.freebusy
https://www.googleapis.com/auth/calendar.events.readonly
```

**4e. Add yourself as a test user.** **OAuth consent screen →
Audience → Test users → Add users** → your Gmail address.

> While the app stays in **Testing**, Google expires refresh tokens
> after **7 days** and delivery will start failing with
> `invalid_grant: Token has been expired or revoked`. Publishing the
> app (**Audience → Publish app**) stops that clock. External +
> unverified is fine for a personal tool.

**4f. Copy the client ID and secret** from the credentials page.

## Step 5 — Fill in `.env`

```bash
cp .env.example .env
```

Then edit `.env`:

```
GOOGLE_OAUTH_CLIENT_ID=<from step 4f>
GOOGLE_OAUTH_CLIENT_SECRET=<from step 4f>
PLANE_API_TOKEN=<from step 3a>
PLANE_WORKSPACE_SLUG=<from step 3b>
PLANE_API_HOST_URL=<from step 3c>
ANTHROPIC_API_KEY=<https://console.anthropic.com>
```

`.env` is gitignored. Never commit it, and never paste its values into
a chat or an issue.

## Step 6 — Install the Plane MCP binary

Nothing to install — `uvx` fetches `plane-mcp-server` (v0.3.x, PyPI)
on demand at first use. Confirm `uvx --version` works; it ships with
`uv` from step 1.

> Do **not** install the npm `@makeplane/plane-mcp-server` — that is an
> unmaintained 0.1.5 TypeScript build with a different, flat tool
> surface.

## Step 7 — Register and authenticate the three MCP servers

```bash
./scripts/setup-mcps.sh --login
```

The script reads `.env`, registers `gmail`, `gcal`, and `plane` at user
scope, and opens a browser for the two Google consent flows. It is
idempotent — safe to re-run.

Confirm all three:

```bash
claude mcp list
```

You want `✔ Connected` on each. **`! Needs authentication` means
registered but not consented** — run `claude mcp login gmail` and
`claude mcp login gcal`. Do this before any scheduled run: the SDK
entrypoint is non-interactive and cannot open a consent flow.

Manual registration, headless boxes, and the `client_secret is
missing` error are all covered in
[`mcp-servers.md`](mcp-servers.md).

## Step 8 — Authorize the send path

Separate from the MCPs: the MCP servers only read. Delivery is done by
`run.py` through the Gmail API, with its own token in your OS keyring.

```bash
uv run python -m app_sdk.auth --setup
```

One browser consent for the `gmail.send` scope. The refresh token is
stored under keyring service `chief-of-staff-agent-sdk`.

If your browser is signed into several Google accounts:

```bash
uv run python -m app_sdk.auth --setup --account you@example.com
```

To replace an existing token later, use `--reauth` — plain `--setup`
short-circuits with "Already authenticated" when a token exists.

## Step 9 — Configure the agent

**9a. Edit `goals.yaml`** and replace every `TODO`:

```yaml
identity:
  user_name: Your Name
  delivery_address: you@example.com   # where the digest is emailed
  timezone: Asia/Karachi              # IANA name; bounds "today"

gmail:
  trusted_domains:
    - yourcompany.com                 # +1 priority when ranking

thresholds:
  inactivity_days: 4                  # days in one state before "stuck"
  calendar_lookahead_days: 7

objectives:
  - key: q4-example
    title: <one-sentence quarterly objective>
    why: <why this matters now>
```

**9b. Pick your Plane projects.** Start Claude Code in the repo and
run the setup command:

```bash
claude
```
```
/plane-setup
```

It lists your workspace's projects, you reply with the numbers you
want, and it writes them to `goals.local.yaml` (gitignored):

```yaml
plane:
  projects:
    - id: 550e8400-e29b-41d4-a716-446655440000
      identifier: ENG
      name: Engineering
```

Re-run it any time to change the selection. Don't hand-edit UUIDs.

## Step 10 — Smoke test

Interactive, in `claude`:

```
/plane-standup      # Plane only — fastest check that MCP + jq work
/triage             # email only, read-only
/morning-digest     # the full fan-out
```

Then the scheduled path, without sending:

```bash
uv run python -m app_sdk.run --dry-run
```

That prints the digest HTML instead of emailing it. When it looks
right, send for real:

```bash
uv run python -m app_sdk.run
```

Other flags: `--command /triage`, `--to someone@example.com`.

## Step 11 — Schedule the daily run

macOS only — it installs a launchd job:

```bash
./scripts/install-schedule.sh              # daily at 08:00
HOUR=7 MINUTE=30 ./scripts/install-schedule.sh   # or pick a time
```

Time defaults to 08:00 machine-local; override with the `HOUR` /
`MINUTE` environment variables. To remove it later:

```bash
launchctl bootout gui/$UID ~/Library/LaunchAgents/com.chief-of-staff-agent-sdk.plist
rm ~/Library/LaunchAgents/com.chief-of-staff-agent-sdk.plist
```

On Linux, use cron to run `uv run python -m app_sdk.run` instead — the
script refuses to run there.

---

## Verification checklist

- [ ] `jq --version`, `uv --version`, `claude --version` all work
- [ ] `claude mcp list` shows gmail, gcal, plane as `✔ Connected`
- [ ] `.env` has all five values; no `TODO` left in `goals.yaml`
- [ ] `goals.local.yaml` exists with at least one project
- [ ] `uv run python -m app_sdk.run --dry-run` prints a digest
- [ ] The digest names teammates, not raw UUIDs
- [ ] A real run lands in your inbox

## Permissions — nothing to do

Permission rules ship in `.claude/settings.json`, committed so a fresh
clone works: the read-only MCP tools plus the `jq` calls
`plane-fetcher` needs are allowed, and every Gmail and Calendar
mutation is denied by name. `.claude/settings.local.json` stays
gitignored for per-machine overrides.

Plane is different. Its v0.3.x tools are action-dispatch — one tool per
resource, so `workitem` serves `list` *and* `delete` — and a deny rule
on the tool name would block reads too. Read-only is enforced instead
by a `PreToolUse` hook, `scripts/plane-readonly-guard.sh`, which
default-denies any `action` outside a read allowlist and also blocks
`pql`.

Adding a rule everyone needs? Put it in `settings.json`. File rules
must be written `Edit(path)` — a `Write(path)` rule is silently never
matched.

## When something breaks

| Symptom | Go to |
|---|---|
| MCP won't connect or authenticate | [`mcp-servers.md`](mcp-servers.md) §Troubleshooting |
| `invalid_grant` on send | Step 4e above — publish the consent screen, then `--reauth` |
| Digest shows UUIDs, or Plane is slow | [`sdk-setup.md`](sdk-setup.md) §Troubleshooting |
| Ran but no email arrived | [`sdk-setup.md`](sdk-setup.md) §Troubleshooting |
| What the agent may and may not do | `CLAUDE.md` Part 1 |
