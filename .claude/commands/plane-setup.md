# /plane-setup — Pick Plane Projects to Watch

## Description
One-time interactive setup. Lists every project in your Plane workspace,
lets you choose which ones the morning digest should include, and writes
your picks to `goals.local.yaml` (gitignored). Re-run any time to change
the selection.

Replaces hand-editing UUIDs into `goals.yaml`.

## Preconditions
- Plane MCP connected (see `docs/mcp-servers.md`).

## Instructions

### Step 1: List projects

Run `./scripts/plane-projects.sh`. It returns
`[{id, identifier, name}, ...]` sorted by name.

> Not an MCP call: plane-mcp-server v0.3.x's `project(action="list")`
> hits a Cloud-only endpoint that 404s on self-hosted Plane Community
> Edition (makeplane/plane-mcp-server#171, #188). The script reads the
> documented v1 REST endpoint directly. Read-only GET.

`identifier` is the short prefix Plane uses in readable issue IDs (e.g.
`ARBISOFTOPEN` for `ARBISOFTOPEN-502`). It's needed later by
`/plane-standup` and `/morning-digest` for issue hydration. Capture it
here so those commands never need the project list again.

### Step 2: Present the menu

Print, exactly:

```
Your Plane projects:
  1. <name>        (id: <first-8-chars-of-uuid>…)
  2. <name>        (id: …)
  ...

Reply with numbers to include (comma-separated), e.g. "1, 3" — or "all".
```

### Step 3: Wait for the user's reply, then parse it

- `"all"` → include every project from step 1.
- `"1, 3"` → include the projects at those positions.
- Anything unparseable → print one clarification asking for numbers or
  `all`, then stop if the second reply also fails.

### Step 4: Write `goals.local.yaml`

Merge the selection into `goals.local.yaml` at repo root. Shape:

```yaml
plane:
  projects:
    - id: 550e8400-e29b-41d4-a716-446655440000
      identifier: ENG
      name: Engineering
    - id: 8f14e45f-3c74-4b8b-9f3d-2a1c5d6e7f80
      identifier: INFRA
      name: Infra
```

Rules:
- If the file exists, replace only the `plane.projects` key; preserve
  every other key.
- If the file does not exist, create it containing just the `plane`
  block above.
- Never touch `goals.yaml` (the committed template).

### Step 5: Confirm

Print:

```
Saved <N> project(s) to goals.local.yaml:
  - <name>
  - <name>

Run /plane-standup or /morning-digest to use them.
```

## Guardrails
- Read-only against Plane — one GET against the projects endpoint.
- Never write to `goals.yaml` (committed) — only `goals.local.yaml`
  (gitignored).
- Never call any Plane write tool (`create_*`, `update_*`, `delete_*`,
  `add_*_comment`, etc.).
