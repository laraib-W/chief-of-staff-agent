"""Domain memory access — seen_emails, issue_stuck_since, digest_log (specs.md §5).

DDL is idempotent (``CREATE TABLE IF NOT EXISTS``) so ``bootstrap`` can be
called safely on every run.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
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


def upsert_stuck(path: Path, issue_id: str, today: date) -> date:
    """Record that ``issue_id`` is stuck as of ``today``.

    Inserts a new row with ``stuck_since = last_confirmed = today`` if the
    issue is not tracked yet; otherwise bumps only ``last_confirmed``.
    Returns the effective ``stuck_since`` date so callers can render
    "stuck for N days" without a second query.
    """
    today_iso = today.isoformat()
    with connect(path) as conn:
        row = conn.execute(
            "SELECT stuck_since FROM issue_stuck_since WHERE issue_id = ?",
            (issue_id,),
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO issue_stuck_since (issue_id, stuck_since, last_confirmed)"
                " VALUES (?, ?, ?)",
                (issue_id, today_iso, today_iso),
            )
            conn.commit()
            return today
        conn.execute(
            "UPDATE issue_stuck_since SET last_confirmed = ? WHERE issue_id = ?",
            (today_iso, issue_id),
        )
        conn.commit()
        return date.fromisoformat(row["stuck_since"])


def prune_stuck(path: Path, current_ids: set[str]) -> int:
    """Delete rows for issues no longer stuck. Returns number of rows removed.

    ``current_ids`` is the set of issue IDs still classified as stuck by
    this run. Anything else in the table has recovered and should be
    forgotten so the next stuck streak starts a new count.
    """
    with connect(path) as conn:
        existing = {
            row["issue_id"]
            for row in conn.execute("SELECT issue_id FROM issue_stuck_since")
        }
        stale = existing - current_ids
        if not stale:
            return 0
        conn.executemany(
            "DELETE FROM issue_stuck_since WHERE issue_id = ?",
            [(issue_id,) for issue_id in stale],
        )
        conn.commit()
        return len(stale)


def get_all_stuck(path: Path) -> dict[str, date]:
    """Return ``{issue_id: stuck_since}`` for every tracked issue."""
    with connect(path) as conn:
        return {
            row["issue_id"]: date.fromisoformat(row["stuck_since"])
            for row in conn.execute(
                "SELECT issue_id, stuck_since FROM issue_stuck_since"
            )
        }
