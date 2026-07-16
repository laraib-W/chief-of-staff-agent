# Contributing

Guidelines for contributing to the Morning Chief-of-Staff Agent. This applies to
all contributors, including the initial two-engineer team.

---

## 1. Repository Structure

```
chief-of-staff-agent/
├── app/
│   ├── graph/
│   │   └── workflow.py          # StateGraph wiring only
│   ├── nodes/                   # one file per node
│   │   ├── fetch_email.py
│   │   ├── fetch_calendar.py
│   │   ├── fetch_plane.py
│   │   ├── classify_email.py
│   │   ├── analyze_day.py
│   │   ├── assess_team.py
│   │   ├── correlate.py
│   │   └── render_digest.py
│   ├── providers/               # all external I/O lives here
│   │   ├── gmail.py
│   │   ├── calendar.py
│   │   ├── plane.py
│   │   └── llm.py
│   ├── schemas/                 # Pydantic models
│   │   ├── email.py
│   │   ├── calendar.py
│   │   ├── plane.py
│   │   └── digest.py
│   ├── storage/
│   │   ├── checkpoint.py        # LangGraph SqliteSaver setup
│   │   ├── memory.py            # domain memory (seen emails, stuck-since)
│   │   └── runs.py              # run audit log
│   ├── prompts/                 # LLM prompt templates
│   ├── config/                  # config.yaml loading and validation
│   └── templates/               # Jinja2 HTML digest templates
├── tests/
│   ├── fixtures/                # recorded API responses for replay mode
│   ├── unit/                    # per-node and per-provider tests
│   ├── integration/             # full-pipeline tests with mocked APIs
│   └── evaluation/              # quality evaluation scripts
├── docs/
│   ├── specs.md
│   ├── adr/                     # Architecture Decision Records
│   └── ...
├── config.yaml
├── .env.example                 # template, never real credentials
├── .gitignore
├── SECURITY.md
├── CONTRIBUTING.md
├── EVALUATION.md
└── README.md
```

**Key principle:** Nodes are pure functions that transform state. Providers handle
all external I/O. Never import an API client inside a node file — always go through
a provider.

---

## 2. Coding Standards

### 2.1 Language and Runtime

- Python 3.11+.
- Type hints on all function signatures. Use `TypedDict` for the agent state,
  Pydantic `BaseModel` for schemas.
- No `# type: ignore` without a comment explaining why.

### 2.2 Style

- **Formatter:** `ruff format` (Black-compatible). Run before every commit.
- **Linter:** `ruff check` with the default rule set plus `I` (isort),
  `UP` (pyupgrade), `S` (bandit security checks), and `PT` (pytest style).
- **Line length:** 88 characters (ruff default).
- **Imports:** Sorted by `ruff` (isort-compatible). Standard library → third-party
  → local. No wildcard imports.
- **Docstrings:** Required on all public functions and classes. Use Google style.
  Nodes must document their input state keys, output state keys, and failure
  behavior.

### 2.3 Naming Conventions

- Files: `snake_case.py`.
- Classes: `PascalCase`.
- Functions and variables: `snake_case`.
- Constants: `UPPER_SNAKE_CASE`.
- Config keys: `snake_case` in YAML, matching the Python field name.

### 2.4 Error Handling

- Nodes must never raise unhandled exceptions. Sensor failures write to
  `errors[sensor_name]` and return empty data. Judgment node failures fall back
  to deterministic defaults.
- Use specific exception types. Never `except Exception` without re-raising or
  logging.
- Log all errors with `structlog` (structured logging). Include the node name,
  the error type, and enough context to reproduce.

### 2.5 Secrets

- Never hardcode credentials, API keys, or tokens.
- Never log credential values, even at DEBUG level.
- The `detect-secrets` pre-commit hook must pass before any commit.

---

## 3. Testing Requirements

### 3.1 Test Categories

| Category     | Location              | Runs in CI? | External calls? |
|--------------|-----------------------|-------------|-----------------|
| Unit         | `tests/unit/`         | Yes         | No              |
| Integration  | `tests/integration/`  | Yes         | No (mocked)     |
| Replay       | `tests/integration/`  | Yes         | No (fixtures)   |
| Evaluation   | `tests/evaluation/`   | Manual      | LLM only        |
| Live smoke   | manual                | No          | Yes             |

### 3.2 What Must Be Tested

**Every node** must have unit tests covering:
- Happy path with representative input.
- Empty input (e.g., no emails, no issues).
- Malformed input (e.g., missing fields in API response).
- Error propagation (sensor failure → `errors` dict populated correctly).

**Every Pydantic schema** must have tests for:
- Valid construction with all fields.
- Validation rejection of invalid data (wrong types, out-of-range values,
  missing required fields).
- Edge cases: empty strings, null optionals, boundary values.

**Every provider** must have tests for:
- Parsing real API response fixtures into typed objects.
- Handling API error responses (401, 403, 429, 500).
- Email sanitization edge cases (nested quotes, unusual signatures, Unicode).

**Replay mode** must run as a CI job:
- `python -m app.run --replay` exits 0 and produces a valid digest HTML.
- Digest HTML passes structural validation (expected sections present, no
  empty sections without corresponding error banners).

### 3.3 Coverage

- Target: 85% line coverage for `app/nodes/` and `app/providers/`.
- LLM prompt content is not covered by line coverage — that's what the evaluation
  suite (EVALUATION.md) is for.
- Coverage is reported in CI but does not block merges. A coverage drop in a PR
  should be explained in the PR description.

### 3.4 Test Naming

```python
# Pattern: test_{function_name}_{scenario}_{expected_outcome}
def test_classify_emails_empty_inbox_returns_empty_list():
    ...

def test_assess_team_overdue_issue_sets_attention_status():
    ...

def test_gmail_provider_expired_token_writes_error():
    ...
```

---

## 4. Branching Strategy

### 4.1 Branch Model

- **`main`** — always deployable. Protected: no direct pushes.
- **`feature/{description}`** — feature branches.
  Examples: `feature/scaffold`, `feature/plane-sensor`,
  `feature/email-classification`, `feature/github-notifications`,
  `feature/open-pr-review`.
- **`fix/{description}`** — bug fixes. Example: `fix/gmail-token-refresh`.
- **`docs/{description}`** — documentation-only changes.

### 4.2 Rules

- Branch from `main`, merge back to `main` via pull request.
- Keep branches short-lived (< 1 week preferred). Break large features into
  multiple PRs if needed.
- Rebase on `main` before opening a PR to keep history linear.
- Delete branches after merge.

---

## 5. Pull Request Checklist

Every PR must satisfy all applicable items before merge. Copy this checklist into
the PR description:

```markdown
### PR Checklist

**Code quality:**
- [ ] `ruff format` passes with no changes needed
- [ ] `ruff check` passes with no warnings
- [ ] All new functions have type hints and docstrings
- [ ] No credentials, tokens, or secrets in the diff
- [ ] `detect-secrets` pre-commit hook passes

**Testing:**
- [ ] New/changed code has unit tests
- [ ] Existing tests still pass (`pytest tests/unit tests/integration`)
- [ ] Replay mode still works (`python -m app.run --replay` exits 0)
- [ ] Schema validation tests cover any new/changed Pydantic models

**Documentation:**
- [ ] Docstrings updated for changed functions
- [ ] ADR written if a significant technical decision was made
- [ ] specs.md updated if behavior or schemas changed
- [ ] EVALUATION.md updated if new LLM-dependent behavior was added

**Security (if applicable):**
- [ ] New credentials documented in SECURITY.md §2.1
- [ ] New external API calls go through a provider, not directly from a node
- [ ] Email-derived data is sanitized before entering state
- [ ] No new write actions against external systems (v1 invariant)

**Reviewer notes:**
- [ ] PR description explains *why*, not just *what*
- [ ] Breaking changes (if any) are called out
```

---

## 6. Commit Messages

Follow Conventional Commits:

```
<type>(<scope>): <short summary>

<optional body — explain *why*, not *what*>
```

**Types:** `feat`, `fix`, `test`, `docs`, `refactor`, `chore`, `ci`.

**Scopes:** `graph`, `nodes`, `providers`, `schemas`, `storage`, `prompts`,
`templates`, `config`, `cli`.

**Examples:**

```
feat(nodes): add classify_email node with batched LLM call

Implements ADR-005: LLM handles classification and ask extraction;
urgency enum and deadline parsing validated by Pydantic schema.

fix(providers): handle Gmail 429 rate limit with exponential backoff

test(schemas): add edge case tests for EmailAction with null deadline

docs(adr): record decision to reject custom LLMProvider abstraction
```

---

## 7. Development Setup

```bash
# Clone and enter the repo
git clone <repo-url> && cd chief-of-staff-agent

# Create a virtual environment
python -m venv .venv && source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt   # ruff, pytest, detect-secrets, etc.

# Copy the environment template and fill in your credentials
cp .env.example .env
chmod 600 .env

# Install pre-commit hooks
pre-commit install

# Verify everything works
ruff check .
pytest tests/unit
python -m app.run --replay
```

---

## 8. Code Review Expectations

- Every PR requires at least one approving review before merge.
- Reviewers should focus on correctness, security (especially around credential
  handling and email sanitization), and adherence to the node/provider separation.
- Style nits are handled by ruff, not by reviewers. Don't comment on formatting
  if the linter passes.
- If a PR changes LLM prompt content, the reviewer should run replay mode and
  inspect the digest output for quality regressions.
- Disagreements are resolved by discussion, not by seniority. If consensus isn't
  reached, write an ADR documenting the trade-offs and the decision.
