#!/usr/bin/env bash
# PreToolUse guard for the Plane MCP (v0.3.x, action-dispatch surface).
#
# Why this exists: v0.3.x collapsed ~47 flat tools into 30 tools that each
# take an `action`. One tool now covers both reads and writes -- `workitem`
# does list AND create/update/delete -- so a permission deny rule on the
# tool NAME can no longer separate them without also blocking reads.
# CLAUDE.md 1.1 requires read-only against Plane, so the separation moves
# here, keyed on the action argument.
#
# Default-deny: an action must be named in READ_ACTIONS to pass.
#
# Also blocks `pql`. Plane self-hosted CE does not implement it and DRF
# ignores unknown query params, so a filtered read returns the FULL result
# set with HTTP 200 -- see makeplane/plane-mcp-server#192. A silently
# dropped filter produces a confidently wrong digest, which is worse than
# a failed one (CLAUDE.md 1.3).
set -euo pipefail

READ_ACTIONS="list list_project list_workspace list_archived list_roles retrieve retrieve_role count count_workspace me search"
NO_ACTION_TOOLS="get_pql_reference"

payload=$(cat)
tool=$(printf '%s' "$payload" | jq -r '.tool_name // ""')
action=$(printf '%s' "$payload" | jq -r '.tool_input.action // ""')
pql=$(printf '%s' "$payload" | jq -r '.tool_input.pql // ""')

deny() {
  jq -cn --arg reason "$1" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: $reason
    }
  }'
  exit 0
}

# Only police the Plane server; anything else passes untouched.
case "$tool" in
  mcp__plane__*) ;;
  *) exit 0 ;;
esac

if [ -n "$pql" ]; then
  deny "Blocked: pql is silently ignored on self-hosted Plane CE and returns the FULL unfiltered result set with HTTP 200 (makeplane/plane-mcp-server#192). Omit pql and filter client-side with jq."
fi

if [ -z "$action" ]; then
  for t in $NO_ACTION_TOOLS; do
    [ "$tool" = "mcp__plane__$t" ] && exit 0
  done
  deny "Blocked: ${tool} was called without an 'action'. This guard is default-deny; name a read action explicitly."
fi

for a in $READ_ACTIONS; do
  [ "$action" = "$a" ] && exit 0
done

deny "Blocked: '${action}' is not a read action on ${tool}. CLAUDE.md 1.1 makes this agent read-only against Plane. Allowed: ${READ_ACTIONS}."
