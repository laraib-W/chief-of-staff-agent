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
| `uvx` | `uvx --version` | ships with `uv` — nothing extra to install |

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
```

There is no `ANTHROPIC_API_KEY` here on purpose. This branch never calls
the Anthropic API directly — `app_sdk/run.py` drives the Claude Code
CLI, which authenticates with whatever account you are already logged
into (`claude` → `/login`). Only the LangGraph pipeline in `app/` on
`main` reads that variable.

> Setting `ANTHROPIC_API_KEY` anyway is not harmless: Claude Code
> prefers it when present, so every run would bill per-token against
> the API console instead of your Claude subscription.

`.env` is gitignored. Never commit it, and never paste its values into
a chat or an issue.

## Step 6 — Register and authenticate the three MCP servers

```bash
./scripts/setup-mcps.sh --login
```

The script reads `.env`, registers `gmail`, `gcal`, and `plane` at user
scope, and opens a browser for the two Google consent flows. It is
idempotent — safe to re-run.

`plane` is registered as `uvx plane-mcp-server stdio` — `uvx` fetches
v0.3.x from PyPI on first use, so there is nothing to install. Do
**not** install the npm `@makeplane/plane-mcp-server`; that is an
unmaintained 0.1.5 TypeScript build with a different, flat tool surface
these skills do not target.

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

## Step 7 — Authorize the send path

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

## Step 8 — Configure the agent

**8a. Copy the template and edit it:**

```bash
cp goals.example.yaml goals.yaml
```

Open `goals.yaml`. Most of it already works — `timezone`,
`trusted_domains`, and both thresholds ship with sensible values. **Three
things need you:**

```yaml
identity:
  user_name: TODO          # -> your first name; the digest greets you by it
  delivery_address: TODO   # -> your inbox, e.g. you@arbisoft.com

objectives:
  - key: TODO              # -> short slug, e.g. q4-latency
    title: TODO            # -> one sentence
    why: TODO              # -> why it matters right now
```

Leave `plane:` commented out — `/plane-setup` writes it in 8b.

Check nothing was missed:

```bash
grep -n TODO goals.yaml    # no output = done
```

What you can change later, and what it affects:

| Key | Effect |
|---|---|
| `identity.timezone` | Bounds "today" for the calendar section. IANA name. |
| `gmail.trusted_domains` | Senders on these domains get +1 priority when ranking. |
| `thresholds.inactivity_days` | Days in one state before an issue is flagged stuck. |
| `thresholds.calendar_lookahead_days` | How far ahead the digest lists events. |
| `objectives` | Referenced when ranking priorities. Re-read every run. |

`goals.yaml` is gitignored; `goals.example.yaml` is the committed
template. Every value is specific to you — your name, your inbox, your
objectives — so the live copy is never tracked.

> It used to work the other way: `goals.yaml` was committed with `TODO`
> placeholders and nobody was supposed to commit the filled-in version.
> That lasted two commits before a real name and delivery address were
> published. Ignoring the live file is what actually prevents it.

Skip this step and the run stops with a named error rather than mailing
your digest somewhere wrong.

**8b. Pick your Plane projects.** Start Claude Code in the repo and
run the setup command:

```bash
claude
```
```
/plane-setup
```

It lists your workspace's projects, you reply with the numbers you
want, and it writes them to `goals.yaml` (gitignored):

```yaml
plane:
  projects:
    - id: 550e8400-e29b-41d4-a716-446655440000
      identifier: ENG
      name: Engineering
```

Re-run it any time to change the selection. Don't hand-edit UUIDs.

## Step 9 — Smoke test

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

## Step 10 — Schedule the daily run

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
- [ ] `.env` has all five values
- [ ] `goals.yaml` exists, copied from the example, with no `TODO` left
- [ ] `goals.yaml` lists at least one project under `plane.projects`
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
