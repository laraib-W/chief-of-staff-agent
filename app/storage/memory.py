"""Domain memory access — seen_emails, issue_stuck_since, digest_log (specs.md §5).

DDL is idempotent (``CREATE TABLE IF NOT EXISTS``) so ``bootstrap`` can be
called safely on every run.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

DDL = """
CREATE TABLE IF NOT EXISTS seen_emails (
    message_id     TEXT PRIMARY KEY,
    first_seen_at  TEXT NOT NULL,
    classification TEXT NOT NULL
        CHECK (classification IN ('needs_reply','waiting','fyi','ignore'))
);

CREATE INDEX IF NOT EXISTS idx_seen_emails_first_seen_at
    ON seen_emails(first_seen_at DESC);

CREATE TABLE IF NOT EXISTS issue_stuck_since (
    issue_id        TEXT PRIMARY KEY,
    stuck_since     TEXT NOT NULL,
    last_confirmed  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_issue_stuck_last_confirmed
    ON issue_stuck_since(last_confirmed);

CREATE TABLE IF NOT EXISTS digest_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    delivered_at  TEXT NOT NULL,
    run_id        INTEGER NOT NULL,
    digest_html   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_digest_log_delivered_at
    ON digest_log(delivered_at DESC);

CREATE INDEX IF NOT EXISTS idx_digest_log_run_id
    ON digest_log(run_id);
"""


@contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def bootstrap(path: Path) -> None:
    with connect(path) as conn:
        conn.executescript(DDL)
        conn.commit()
