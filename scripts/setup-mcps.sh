#!/usr/bin/env bash
# Register the gmail and gcal MCP servers non-interactively from
# the credentials in .env.
#
# Idempotent: any existing server under `gmail` / `gcal` is
# removed first, so re-running is safe after rotating a credential.
#
# Prereqs — see docs/mcp-servers.md for the details:
#   Google (gmail + gcal):
#     - APIs enabled on the project behind the OAuth client:
#         gmail.googleapis.com, gmailmcp.googleapis.com,
#         calendar-json.googleapis.com, calendarmcp.googleapis.com
#     - OAuth consent screen has both gmail and calendar scopes
#     - Web-application OAuth client with http://localhost:8765/callback
#       registered as an authorized redirect URI
#   Plane:
#     - Plane needs no MCP server; see scripts/plane.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"
CALLBACK_PORT=8765

usage() {
  cat <<EOF
usage: $(basename "$0") [--login] [gmail] [gcal]

Registers the named MCP servers. With no server named, registers both.
Only credentials for the selected servers are required in .env.

  --login   After registering, run \`claude mcp login\` for each selected
            server. Opens a browser tab per server and blocks until you
            consent, so it requires a terminal.

Plane has no MCP server. It is read through scripts/plane.sh (GET-only
against the v1 REST API) — see .claude/agents/plane-fetcher.md for why.

Examples:
  $(basename "$0")                  # gmail + gcal, register only
  $(basename "$0") --login          # register both, then consent
  $(basename "$0") gmail            # only re-run gmail
  $(basename "$0") --login gcal     # register gcal and consent to it
EOF
}

# Parse args → the SERVERS set plus the --login toggle. With no server
# named (flags don't count), default to all three.
DO_LOGIN=0
SERVERS=()
for arg in "$@"; do
  case "$arg" in
    gmail|gcal) SERVERS+=("$arg") ;;
    --login) DO_LOGIN=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "error: unknown argument '$arg' — expected --login or one of: gmail, gcal" >&2
       usage >&2
       exit 1 ;;
  esac
done
if [ ${#SERVERS[@]} -eq 0 ]; then
  SERVERS=(gmail gcal)
fi

selected() {
  local target="$1"
  for s in "${SERVERS[@]}"; do
    [ "$s" = "$target" ] && return 0
  done
  return 1
}

if [ ! -f "$ENV_FILE" ]; then
  echo "error: $ENV_FILE not found — copy .env.example and fill in the credentials." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

# Plane's MCP expects PLANE_API_KEY, but the .env template names it
# PLANE_API_TOKEN (matches the `app/` branch on main). Accept either.
PLANE_API_KEY="${PLANE_API_KEY:-${PLANE_API_TOKEN:-}}"

# Only require credentials for the servers we're actually registering,
# so `./setup-mcps.sh gmail gcal` doesn't fail because Plane's env vars
# happen to be blank in a fresh clone.
if selected gmail || selected gcal; then
  : "${GOOGLE_OAUTH_CLIENT_ID:?missing GOOGLE_OAUTH_CLIENT_ID in .env}"
  : "${GOOGLE_OAUTH_CLIENT_SECRET:?missing GOOGLE_OAUTH_CLIENT_SECRET in .env}"
fi

register_google() {
  local name="$1" url="$2"
  # `logout` clears the cached OAuth record for this server. Without it a
  # re-run can re-consent successfully and still fail the code->token
  # exchange, because the stale record predates the client secret.
  claude mcp logout "$name" >/dev/null 2>&1 || true
  claude mcp remove --scope user "$name" >/dev/null 2>&1 || true
  # `--client-secret` normally prompts with masked input; MCP_CLIENT_SECRET
  # supplies it non-interactively. Verified on claude CLI 2.1.236.
  #
  # NOTE: the CLI keeps the secret in its own credential store, NOT in
  # ~/.claude.json — a registered server shows only clientId and
  # callbackPort under `oauth` there. That is the healthy shape; do not
  # "fix" it by writing clientSecret into the JSON. Whether the secret
  # actually landed is proven by `claude mcp login "$name"` succeeding,
  # which is a browser flow and therefore not scriptable here.
  MCP_CLIENT_SECRET="$GOOGLE_OAUTH_CLIENT_SECRET" \
  claude mcp add \
    --scope user --transport http \
    --client-id "$GOOGLE_OAUTH_CLIENT_ID" \
    --client-secret \
    --callback-port "$CALLBACK_PORT" \
    "$name" "$url"
}

if selected gmail; then
  echo "Registering gmail..."
  register_google gmail https://gmailmcp.googleapis.com/mcp/v1
fi

if selected gcal; then
  echo "Registering gcal..."
  register_google gcal https://calendarmcp.googleapis.com/mcp/v1
fi

echo
echo "Registered. Verify with: claude mcp list"

GOOGLE_SERVERS=()
if selected gmail; then GOOGLE_SERVERS+=(gmail); fi
if selected gcal; then GOOGLE_SERVERS+=(gcal); fi

if [ ${#GOOGLE_SERVERS[@]} -eq 0 ]; then
  exit 0
fi

if [ "$DO_LOGIN" -eq 1 ]; then
  # `claude mcp login` opens a browser and blocks until consent returns
  # on the loopback callback. Without a terminal it would hang forever,
  # so fail fast with the commands to run by hand instead.
  if [ ! -t 0 ] || [ ! -t 1 ]; then
    echo "error: --login needs a terminal (it opens a browser and waits)." >&2
    echo "       Run these yourself once you have one:" >&2
    for name in "${GOOGLE_SERVERS[@]}"; do
      echo "         claude mcp login $name" >&2
    done
    exit 1
  fi

  for name in "${GOOGLE_SERVERS[@]}"; do
    echo
    echo "Authenticating $name (a browser tab will open)..."
    if ! claude mcp login "$name"; then
      echo >&2
      echo "error: consent for $name did not complete." >&2
      echo "       Retry with: claude mcp login $name" >&2
      echo "       Over SSH, add --no-browser to get a pasteable URL." >&2
      echo "       If it reports 'client_secret is missing', see the" >&2
      echo "       troubleshooting section in docs/mcp-servers.md." >&2
      exit 1
    fi
  done

  echo
  echo "Done. All selected servers should now show as connected:"
  echo "  claude mcp list"
  exit 0
fi

echo
echo "Not authenticated yet — the servers above will show"
echo "'! Needs authentication' until you consent once each:"
echo
for name in "${GOOGLE_SERVERS[@]}"; do
  echo "  claude mcp login $name"
done
echo
echo "(or re-run this script with --login to do it now)"
echo
echo "Each opens a browser tab (add --no-browser over SSH). Do this before"
echo "running app_sdk/run.py — the SDK entrypoint is non-interactive and"
echo "cannot perform the consent flow itself."
