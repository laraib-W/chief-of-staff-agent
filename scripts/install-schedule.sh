#!/usr/bin/env bash
# Install a macOS LaunchAgent that runs `python -m app_sdk.run` daily.
#
# Usage:
#   ./scripts/install-schedule.sh                # default: 08:00 local time
#   HOUR=7 MINUTE=30 ./scripts/install-schedule.sh
#
# The plist is resolved from scripts/chief-of-staff-agent-sdk.plist.template
# and written to ~/Library/LaunchAgents/. Re-running replaces the existing
# agent (bootout then bootstrap), so it's safe to run repeatedly.
#
# One-off test after install:
#   launchctl kickstart -k gui/$UID/com.chief-of-staff-agent-sdk
#
# Uninstall:
#   launchctl bootout gui/$UID ~/Library/LaunchAgents/com.chief-of-staff-agent-sdk.plist
#   rm ~/Library/LaunchAgents/com.chief-of-staff-agent-sdk.plist

set -euo pipefail

if [[ "$(uname)" != "Darwin" ]]; then
  echo "error: this script is macOS-only (launchd). On Linux use cron —" >&2
  echo "  crontab -e" >&2
  echo "  0 8 * * * cd $(pwd) && $(command -v uv || echo uv) run python -m app_sdk.run" >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="$REPO_ROOT/scripts/chief-of-staff-agent-sdk.plist.template"
LABEL="com.chief-of-staff-agent-sdk"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
HOUR="${HOUR:-8}"
MINUTE="${MINUTE:-0}"

UV_BIN="$(command -v uv || true)"
if [[ -z "$UV_BIN" ]]; then
  echo "error: uv not on PATH. Install uv first (see README)." >&2
  exit 1
fi

# launchd runs with an empty PATH — include uv's dir + standard system paths.
UV_DIR="$(dirname "$UV_BIN")"
PLIST_PATH="$UV_DIR:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

mkdir -p "$(dirname "$TARGET")"

sed \
  -e "s|__UV_BIN__|$UV_BIN|g" \
  -e "s|__REPO_ROOT__|$REPO_ROOT|g" \
  -e "s|__PATH__|$PLIST_PATH|g" \
  -e "s|__HOUR__|$HOUR|g" \
  -e "s|__MINUTE__|$MINUTE|g" \
  "$TEMPLATE" > "$TARGET"

launchctl bootout "gui/$UID" "$TARGET" 2>/dev/null || true
launchctl bootstrap "gui/$UID" "$TARGET"

printf 'Installed: %s\n' "$TARGET"
printf 'Scheduled: daily at %02d:%02d (machine local time)\n' "$HOUR" "$MINUTE"
printf 'Logs: /tmp/chief-of-staff-agent-sdk.{log,err}\n'
printf '\nTrigger a one-off run to verify: launchctl kickstart -k gui/$UID/%s\n' "$LABEL"
