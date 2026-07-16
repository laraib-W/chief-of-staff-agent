"""Run audit log access — runs table (specs.md §5).

Every invocation of ``python -m app.run`` writes one row: ``started_at`` on
entry, then ``finished_at``/``node_durations``/``total_tokens``/``errors``
on completion (crash leaves ``finished_at`` NULL, which is how failed runs
are detected).
"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

DDL = """
CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    node_durations  TEXT NOT NULL DEFAULT '{}'
        CHECK (json_valid(node_durations)),
    total_tokens    INTEGER NOT NULL DEFAULT 0,
    errors          TEXT NOT NULL DEFAULT '{}'
        CHECK (json_valid(errors)),
    config_hash     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_started_at
    ON runs(started_at DESC);
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


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def start_run(path: Path, config_hash: str) -> int:
    with connect(path) as conn:
        cursor = conn.execute(
            "INSERT INTO runs (started_at, config_hash) VALUES (?, ?)",
            (_utc_now(), config_hash),
        )
        conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]


def finish_run(
    path: Path,
    run_id: int,
    node_durations: dict[str, float],
    total_tokens: int,
    errors: dict[str, str | None],
) -> None:
    with connect(path) as conn:
        conn.execute(
            """
            UPDATE runs
               SET finished_at = ?,
                   node_durations = ?,
                   total_tokens = ?,
                   errors = ?
             WHERE id = ?
            """,
            (
                _utc_now(),
                json.dumps(node_durations),
                total_tokens,
                json.dumps(errors),
                run_id,
            ),
        )
        conn.commit()
