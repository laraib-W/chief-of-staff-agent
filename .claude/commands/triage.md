# /triage — Email Triage (read-only)

## Description
Scan the last 24h of email, tier it, and produce a prioritized reading
list. This does **not** draft or send replies — it's a decision aid for
the user's inbox time. If you want the morning digest instead, use `/morning-digest`.

## Preconditions
- Gmail MCP connected (Google's official server —
  `search_threads` / `get_thread`).
- Read-only. Do not draft, label, or otherwise mutate Gmail. Every
  write the MCP exposes (`create_draft`, `create_label`,
  `label_message`, `label_thread`, `unlabel_message`, `unlabel_thread`)
  is denied at the session level. Reply/archive/delete/mark-as-read
  are not offered by this MCP at all.

## Instructions

### Step 1: Fetch (via subagent)

Dispatch one `Agent` call with `subagent_type: "gmail-fetcher"`.
Prompt: "Fetch inbox mail newer than 24h and return the structured
JSON per your contract."

The subagent runs the thread-based Gmail tools, filters out
`noreply`/notifications/drafts/sent, and returns
`{emails: [...], error}` where each email has `msg_id`, `thread_id`,
`sender`, `sender_domain`, `subject`, `date`, and `snippet`. Do not
call `mcp__gmail__*` tools directly from this context — the fetcher
owns raw payload access.

If the subagent returns a non-null `error`, print it and stop.

### Step 2: Tier

Assign one tier per message:

| Tier | Meaning              | Signals                                                                 |
|------|----------------------|-------------------------------------------------------------------------|
| T1   | Respond now          | From `gmail.trusted_domains` in `goals.yaml`, AND contains a direct ask |
| T2   | Handle today         | Contains an ask but sender is not in trusted domains                    |
| T3   | FYI / archive        | Newsletters, notifications, no-reply, auto-generated                    |

If unsure, default to T2 — it's the least destructive misclassification.

### Step 3: Report

Print a table to the transcript. One row per email. Columns: tier,
sender, subject, one-line ask (or "—" for T3). Sort by tier ascending,
then by date descending.

End with a one-line summary: "N T1, M T2, K T3." No recommendations
about what to do — the user decides.

## Guardrails

- Read-only. See `CLAUDE.md` Part 1.1.
- Do not open attachments.
- Do not follow links in message bodies.
