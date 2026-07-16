"""SQLite persistence for the agent (specs.md §5).

Three separate files, deliberately not merged:

- ``checkpoints.db`` — LangGraph resume state (managed by ``SqliteSaver``)
- ``memory.db``     — domain memory across runs
- ``runs.db``       — audit log of every run
"""

from pathlib import Path

DEFAULT_DATA_DIR = Path("data")

CHECKPOINTS_DB = "checkpoints.db"
MEMORY_DB = "memory.db"
RUNS_DB = "runs.db"


def resolve_paths(data_dir: Path | None = None) -> dict[str, Path]:
    base = (data_dir or DEFAULT_DATA_DIR).resolve()
    base.mkdir(parents=True, exist_ok=True)
    return {
        "checkpoints": base / CHECKPOINTS_DB,
        "memory": base / MEMORY_DB,
        "runs": base / RUNS_DB,
    }
