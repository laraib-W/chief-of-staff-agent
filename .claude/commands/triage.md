# /triage — Email Triage (read-only)

## Description
Scan the last 24h of email, tier it, and produce a prioritized reading
list. This does **not** draft or send replies — it's a decision aid for
the user's inbox time. If you want the morning digest instead, use `/morning-digest`.

## Preconditions
- Gmail MCP connected.
- Read-only. Absolutely no `send-email`, `mark-as-read`, `archive`, or
  `delete` calls under any circumstance.

## Instructions

### Step 1: Fetch

Call the gmail MCP to list emails received in the last 24h. Capture
`id`, `sender`, `subject`, `date`, body snippet (~500 chars).

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
