# Security

This document describes the threat model, secret management, trust boundaries,
and operational security posture for the Morning Chief-of-Staff Agent v1.

---

## 1. Threat Model

### 1.1 System Profile

The agent is a local, single-user, read-only tool that runs once daily on a
developer's machine (or a private server). It processes data from three sources
(Gmail, Google Calendar, Plane) and delivers an HTML digest via email. It has no
public-facing endpoints, no web UI, and no inbound network listeners.

### 1.2 Trust Boundaries

```
┌─────────────────────────────────────────────────────────┐
│  TRUSTED: Agent Runtime                                 │
│  ┌───────────┐  ┌────────────┐  ┌───────────────────┐  │
│  │ Nodes     │  │ Providers  │  │ SQLite (3 files)  │  │
│  │ (pure fn) │  │ (API I/O)  │  │ checkpoints/      │  │
│  │           │  │            │  │ memory/runs       │  │
│  └───────────┘  └─────┬──────┘  └───────────────────┘  │
└────────────────────────┼────────────────────────────────┘
                         │ OAuth / API tokens
    ─────────────────────┼──────────────────────────────────
                         │
    ┌────────────────────┼────────────────────────────────┐
    │  SEMI-TRUSTED: External APIs                        │
    │  Gmail API  │  Calendar API  │  Plane API           │
    └────────────────────┼────────────────────────────────┘
                         │
    ─────────────────────┼──────────────────────────────────
                         │
    ┌────────────────────┼────────────────────────────────┐
    │  UNTRUSTED: Email Content                           │
    │  Sender-crafted text, HTML, attachments              │
    └─────────────────────────────────────────────────────┘
```

**Boundary 1 — Agent ↔ External APIs:** Authenticated via OAuth (Google) and API
token (Plane). Scoped to read-only. The agent trusts API response structure but
validates content via Pydantic schemas.

**Boundary 2 — External APIs ↔ Email Content:** Email bodies are fully untrusted.
They are authored by arbitrary external senders and may contain prompt injection
attempts, phishing content, or adversarial text.

### 1.3 Threat Catalog

| ID  | Threat                              | Likelihood | Impact  | Mitigation                                    |
|-----|-------------------------------------|------------|---------|-----------------------------------------------|
| T1  | Prompt injection via email          | Medium     | Low     | Read-only architecture (no write actions to trigger); email sanitization; constrained output schemas; correlation node receives structured objects, not raw text. |
| T2  | OAuth token theft from disk         | Low        | High    | Refresh tokens stored in OS keyring, not files. Client secret in `.env`, never committed. |
| T3  | Plane API token theft               | Low        | Medium  | Token in `.env`, read-only usage limits blast radius. |
| T4  | SQLite data exposure                | Low        | Medium  | Database files excluded from git; contain sanitized data only (no raw email bodies). |
| T5  | Dependency supply chain attack      | Low        | High    | Pin all dependencies; use `pip audit` in CI; review lockfile diffs. |
| T6  | Digest content manipulation         | Low        | Low     | A crafted email could place misleading text in the digest. Impact is limited to the user reading their own digest. |
| T7  | LLM output exfiltration             | Very Low   | Medium  | No outbound network calls from LLM nodes; output is written to state, not sent to external endpoints. |
| T8  | Stale OAuth token → silent failure  | Medium     | Low     | Graceful degradation: digest ships with a banner, not silence. Token refresh monitoring in runs table. |

### 1.4 What is Explicitly Out of Scope for v1

- Network-level attacks (the agent has no inbound listeners).
- Multi-user access control (single user, single machine).
- Encryption at rest for SQLite files (proportionate to a local dev tool; revisit
  for any cloud deployment).
- Formal penetration testing (disproportionate for a personal tool; revisit before
  any Arbisoft-wide rollout).

---

## 2. Secret Management

### 2.1 Credential Inventory

| Credential                  | Storage Location   | Scope            | Rotation Cadence     |
|-----------------------------|--------------------|------------------|----------------------|
| Google OAuth client ID      | `.env`             | Read-only Gmail + Calendar | Rotate if compromised |
| Google OAuth client secret  | `.env`             | Same             | Rotate if compromised |
| Google OAuth refresh token  | OS keyring (`keyring` package) | Same  | Auto-refreshes; re-auth if revoked |
| Plane API token             | `.env`             | GET-only         | Every 90 days or on personnel change |
| LLM API key (Anthropic)    | `.env`             | Model inference  | Every 90 days        |

### 2.2 Rules

- **No credentials in source code, config.yaml, SQLite, or git history.** Ever.
  The `.gitignore` must include `.env`, `*.db`, and any keyring export files.
- **Providers read credentials directly** from environment variables or keyring.
  LangGraph nodes never see tokens in state — the `AgentState` TypedDict has no
  credential fields.
- **`.env` file permissions:** `chmod 600 .env` on creation. Document this in
  the setup guide.
- **Pre-commit hook:** A `detect-secrets` pre-commit hook scans staged files for
  high-entropy strings, API key patterns, and known secret formats. Commits
  containing likely secrets are blocked.

### 2.3 Credential Rotation Procedure

1. Generate the new credential in the source system (Google Cloud Console, Plane
   settings, Anthropic dashboard).
2. Update `.env` with the new value.
3. If the credential is a refresh token, clear the keyring entry and re-authenticate:
   `python -m app.auth --reauth`.
4. Run `python -m app.run --dry-run` to verify the new credential works end-to-end.
5. Revoke the old credential in the source system.
6. Record the rotation in the team's credential log (spreadsheet, 1Password, or
   equivalent).

---

## 3. Email Sanitization Pipeline

Because email is the primary untrusted input, sanitization is detailed here as a
security specification, not just a quality concern.

The `gmail.py` provider applies the following transformations to every message
before it enters `AgentState`:

1. **HTML stripping:** Remove all HTML tags. Preserve only plain text content.
2. **Tracking link removal:** Strip known tracking domains and URL-rewriting
   patterns (e.g., `click.mailchimp.com`, UTM parameters).
3. **Quoted reply removal:** Detect and strip quoted reply chains (lines starting
   with `>`, `On ... wrote:` blocks, Outlook-style separators).
4. **Signature removal:** Strip common email signature patterns (lines after
   `--`, `Sent from my`, `Regards,` followed by a name).
5. **Unicode normalization:** Normalize to NFC form. Strip zero-width characters,
   bidirectional overrides, and other invisible Unicode that could confuse the LLM.
6. **Length truncation:** Cap body text at a configurable maximum (default: 2,000
   characters) to prevent token-stuffing attacks.

The sanitized output is: `{sender, subject, clean_body, date}`. No raw HTML, no
attachment content, no tracking metadata enters the pipeline.

---

## 4. Dependency Security

- **Pin all dependencies** in `requirements.txt` (or `pyproject.toml` with locked
  versions). No floating version specifiers (`>=`, `~=`) in production.
- **Run `pip audit`** in CI on every pull request. Block merges on known
  vulnerabilities with severity ≥ High.
- **Review lockfile diffs** in PRs that update dependencies. Understand what changed
  and why.
- **Minimize the dependency tree.** Prefer standard library solutions where the
  alternative is a small, unmaintained package.
- **Monthly dependency update cycle:** On the first Monday of each month, run
  `pip list --outdated`, review changelogs, update in a dedicated PR, and run
  the full test suite including replay mode.

---

## 5. Data Handling

### 5.1 What is Stored

| Data                         | Where           | Retention        | Contains PII?  |
|------------------------------|-----------------|------------------|----------------|
| Seen email IDs               | `memory.db`     | Indefinite       | No (IDs only)  |
| Issue stuck-since dates      | `memory.db`     | Indefinite       | No             |
| Digest snapshots             | `runs.db`       | Indefinite       | Yes (names, issue titles) |
| Run metadata                 | `runs.db`       | Indefinite       | No             |
| LangGraph checkpoints        | `checkpoints.db`| Prune after 7 days | Transient state |
| Fixture files (replay mode)  | `tests/fixtures/` | Indefinite     | Yes (sanitized) |

### 5.2 Rules

- Raw email bodies are never stored in any database. Only sanitized extracts
  (sender, subject, extracted ask) appear in digest snapshots.
- Fixture files are reviewed for sensitive content before committing. Email
  bodies in fixtures should be sanitized or replaced with synthetic data.
- SQLite database files are excluded from git and from any backup that goes to
  a shared or public location.
- If the agent is ever deployed to a shared server, database files must be
  readable only by the agent's OS user (`chmod 600 *.db`).

---

## 6. Incident Handling

### 6.1 What Constitutes a Security Incident

- A credential appears in git history, logs, or any shared medium.
- An OAuth token is used from an unexpected IP or device (visible in Google
  Security Activity).
- The Plane API token is used for non-GET requests (impossible from the agent,
  but would indicate token compromise).
- A dependency is flagged with a critical CVE that affects the agent's usage
  pattern.

### 6.2 Response Procedure

1. **Contain:** Immediately revoke the compromised credential in the source system.
2. **Assess:** Determine what data the credential could have accessed. For read-only
   tokens, the blast radius is data exposure, not modification.
3. **Rotate:** Generate new credentials and update `.env` / keyring per §2.3.
4. **Scrub:** If a credential was committed to git, rewrite history (`git filter-repo`)
   and force-push. Notify any collaborators to re-clone.
5. **Review:** Check the `runs.db` for anomalous runs (unexpected timestamps,
   unusual error patterns). Check Google Security Activity for OAuth token usage.
6. **Document:** Write a brief incident report: what happened, when it was detected,
   what was done, and what prevention measure was added.

### 6.3 Credential Leak in Git — Quick Reference

```bash
# 1. Install git-filter-repo (if not present)
pip install git-filter-repo

# 2. Remove the file containing the secret from all history
git filter-repo --path .env --invert-paths

# 3. Force-push to remote
git push --force --all

# 4. Rotate the leaked credential immediately
# 5. Notify collaborators to re-clone the repository
```

---

## 7. v2 Security Considerations (Forward-Looking)

When v2 introduces write actions (proposing board changes with human approval),
the security posture changes fundamentally:

- The `interrupt()` human-approval gate becomes a **security boundary**, not just
  a UX feature. It must be hardened against bypass.
- Prompt injection becomes a higher-impact threat (an injected instruction could
  propose a harmful board action that the user rubber-stamps).
- Multi-user support requires access control: who can configure the agent, who
  can approve proposed actions, who can see whose digest.
- Any Arbisoft-wide rollout requires a formal security review, not just this
  document.

These concerns are documented here for awareness but are explicitly out of scope
for v1 implementation.
