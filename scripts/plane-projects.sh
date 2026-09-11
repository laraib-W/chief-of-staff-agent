#!/usr/bin/env bash
# List Plane projects as compact JSON: [{id, identifier, name}, ...]
#
# Why this exists: plane-mcp-server v0.3.x's `project(action="list")`
# calls a Cloud-only endpoint that 404s on self-hosted Plane Community
# Edition (cf. makeplane/plane-mcp-server#171, #188). /plane-setup needs
# the project list once, so it reads the documented v1 REST endpoint
# directly instead. Read-only GET.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
: "${PLANE_API_TOKEN:?missing PLANE_API_TOKEN in .env}"
: "${PLANE_WORKSPACE_SLUG:?missing PLANE_WORKSPACE_SLUG in .env}"
: "${PLANE_API_HOST_URL:?missing PLANE_API_HOST_URL in .env}"
curl -sS --get \
  -H "X-API-Key: $PLANE_API_TOKEN" -H "Accept: application/json" \
  --data-urlencode "fields=id,identifier,name" \
  "$PLANE_API_HOST_URL/api/v1/workspaces/$PLANE_WORKSPACE_SLUG/projects/" \
| jq -c '[(.results // .)[] | {id, identifier, name}] | sort_by(.name)'
