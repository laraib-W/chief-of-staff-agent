#!/usr/bin/env bash
# Read-only Plane fetches for the digest skills. GET only — this script
# issues no POST/PATCH/DELETE, which is the whole read-only guarantee for
# Plane (CLAUDE.md 1.1) and is checkable by reading it.
#
# Why not the Plane MCP:
#   - v0.3.x returns large results INLINE to an SDK subagent, so the agent
#     paginates them into its own context: ~55k tokens and >600s for one
#     project. Writing the payload to a file here makes the jq filtering
#     deterministic instead of dependent on runtime spill behaviour.
#   - project(action="list") 404s on self-hosted Community Edition
#     (makeplane/plane-mcp-server#171, #188).
#   - pql is silently ignored on CE — filtered reads return everything
#     with HTTP 200 (#192) — so filtering has to happen locally regardless.
#
# Usage:
#   plane.sh projects                       -> [{id, identifier, name}, ...]
#   plane.sh members  <project_id>          -> {uuid: display_name, ...}
#   plane.sh fetch    <project_id>          -> caches issues; prints state census
#   plane.sh stubs    <project_id> <active-names-csv> <inactivity_days>
#                                           -> active-bucket stubs, aged and flagged
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
: "${PLANE_API_TOKEN:?missing PLANE_API_TOKEN in .env}"
: "${PLANE_WORKSPACE_SLUG:?missing PLANE_WORKSPACE_SLUG in .env}"
: "${PLANE_API_HOST_URL:?missing PLANE_API_HOST_URL in .env}"
BASE="$PLANE_API_HOST_URL/api/v1/workspaces/$PLANE_WORKSPACE_SLUG"
FIELDS="id,sequence_id,name,state,assignees,labels,target_date,created_at,updated_at"
CACHE=".cache"

get() { curl -sS --get -H "X-API-Key: $PLANE_API_TOKEN" -H "Accept: application/json" "$@"; }

# Follow next_cursor to exhaustion and emit one merged {total_count, results}.
# Plane's documented per_page cap is 100; instances differ, so never assume
# one page holds everything.
fetch_all_issues() {
  local pid="$1" cursor="" page=0 tmp merged
  merged="$(mktemp)"; echo '{"total_count":0,"results":[]}' > "$merged"
  while :; do
    tmp="$(mktemp)"
    if [ -z "$cursor" ]; then
      get --data-urlencode "fields=$FIELDS" --data-urlencode "expand=state" \
          --data-urlencode "per_page=100" "$BASE/projects/$pid/issues/" > "$tmp"
    else
      get --data-urlencode "fields=$FIELDS" --data-urlencode "expand=state" \
          --data-urlencode "per_page=100" --data-urlencode "cursor=$cursor" \
          "$BASE/projects/$pid/issues/" > "$tmp"
    fi
    jq -s '{total_count: (.[1].total_count // .[0].total_count),
            results: (.[0].results + .[1].results)}' "$merged" "$tmp" > "$merged.new"
    mv "$merged.new" "$merged"
    cursor="$(jq -r '.next_cursor // ""' "$tmp")"
    [ "$(jq -r '.next_page_results // false' "$tmp")" = "true" ] || { rm -f "$tmp"; break; }
    rm -f "$tmp"; page=$((page+1)); [ "$page" -gt 50 ] && break
  done
  echo "$merged"
}

case "${1:-}" in
  projects)
    get --data-urlencode "fields=id,identifier,name" "$BASE/projects/" \
    | jq -c '[(if type=="array" then . else .results end)[]
              | {id, identifier, name}] | sort_by(.name)' ;;

  members)
    pid="${2:?usage: plane.sh members <project_id>}"
    get "$BASE/projects/$pid/members/" | jq -c '
      def nm:
        (.display_name // "") as $d
        | (((.first_name // "") + " " + (.last_name // "")) | gsub("^ +| +$";"")) as $f
        | if $d != "" then $d elif $f != "" then $f
          elif (.email // "") != "" then .email else .id end;
      [(if type=="array" then . else .results end)[]
       | {key: .id, value: nm}] | from_entries' ;;

  fetch)
    pid="${2:?usage: plane.sh fetch <project_id>}"
    mkdir -p "$CACHE"
    out="$(fetch_all_issues "$pid")"
    mv "$out" "$CACHE/issues-$pid.json"
    jq -r '"total_issues\t\(.total_count)",
           (.results | group_by(.state.name)[] | "\(length)\t\(.[0].state.name)")' \
      "$CACHE/issues-$pid.json" ;;

  stubs)
    pid="${2:?usage: plane.sh stubs <project_id> <active-csv> <days>}"
    active="${3:?missing active-names csv}"; days="${4:?missing inactivity_days}"
    jq -c --argjson active "$(jq -Rc 'split(",")|map(gsub("^ +| +$";""))' <<<"$active")" \
          --argjson inactivity "$days" '
      [.results[]
       | select(.state.name as $n | $active | index($n))
       | (((now - (.updated_at | sub("\\.[0-9]+Z$";"Z") | fromdateiso8601)) / 86400)
          | floor | if . < 0 then 0 else . end) as $age
       | {issue_id: .id, sequence_id, name,
          state_id: .state.id, state_name: .state.name,
          assignee_ids: (.assignees // []), target_date, updated_at,
          age_in_state_days: $age, is_stuck: ($age >= $inactivity)}]' \
      "$CACHE/issues-$pid.json" ;;

  *) sed -n '/^# Usage:/,/^set -euo/p' "$0" | sed '$d'; exit 2 ;;
esac
