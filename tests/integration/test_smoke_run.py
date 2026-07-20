"""End-to-end smoke tests for python -m app.run (specs.md §8).

Acceptance criteria:
- --dry-run completes in under 10 s.
- A row is inserted into runs.db.runs with finished_at set and an errors JSON
  object that reports each wired sensor's status (specs.md §3.1.1) — gmail is
  the first real sensor, so errors["gmail"] is always present: None on
  success, an error string on failure. Either is fine here; the point is the
  run completes via graceful degradation rather than crashing.
- memory.db.digest_log is NOT written (no delivery node exists yet).
- Both conditions hold with and without --replay.
"""

import json
import subprocess
import sys
import time

import pytest

from app.storage import memory as memory_store
from app.storage import runs as runs_store

_SMOKE_TIMEOUT_SECONDS = 10

_MINIMAL_CONFIG = """\
identity:
  user_name: "Smoke Test User"
  timezone: "UTC"
  delivery_address: "smoke@example.com"
  run_time: "08:00"
gmail:
  fetch_window_hours: 24
plane:
  base_url: "https://api.plane.so"
  workspace_slug: ""
  project_ids: ["proj-smoke-test"]
thresholds:
  inactivity_days: 4
  overdue_grace_days: 0
  load_multiplier: 1.5
  due_soon_horizon_days: 7
llm:
  model: "claude-sonnet-4-6"
  max_tokens_per_node: 4096
  batch_mode: true
"""


@pytest.mark.integration()
@pytest.mark.parametrize(
    "extra_args",
    [[], ["--replay"]],
    ids=["live", "replay"],
)
def test_dry_run_smoke_completes_end_to_end(tmp_path, extra_args):
    """Full pipeline runs end-to-end, persists a run row, skips digest delivery."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(_MINIMAL_CONFIG)
    data_dir = tmp_path / "data"

    cmd = [
        sys.executable,
        "-m",
        "app.run",
        "--config",
        str(config_path),
        "--data-dir",
        str(data_dir),
        "--dry-run",
        *extra_args,
    ]

    start = time.monotonic()
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    elapsed = time.monotonic() - start

    assert result.returncode == 0, (
        f"app.run exited {result.returncode}\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
    assert (
        elapsed < _SMOKE_TIMEOUT_SECONDS
    ), f"smoke run took {elapsed:.2f}s, expected < {_SMOKE_TIMEOUT_SECONDS}s"

    runs_db = data_dir / "runs.db"
    assert runs_db.exists(), "runs.db was not created"

    with runs_store.connect(runs_db) as conn:
        row = conn.execute("SELECT finished_at, errors FROM runs").fetchone()

    assert row is not None, "no row written to runs.db.runs"
    assert row["finished_at"] is not None, "finished_at is NULL — run did not complete"
    errors = json.loads(row["errors"])
    assert "gmail" in errors, f"expected gmail sensor status in errors: {errors}"

    memory_db = data_dir / "memory.db"
    assert memory_db.exists(), "memory.db was not created"

    with memory_store.connect(memory_db) as conn:
        count = conn.execute("SELECT COUNT(*) FROM digest_log").fetchone()[0]

    assert (
        count == 0
    ), f"digest_log should be empty during --dry-run but has {count} row(s)"
