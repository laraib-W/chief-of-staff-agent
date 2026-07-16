"""LangGraph SqliteSaver setup for checkpoints.db (specs.md §5).

``checkpoints.db`` is treated as an opaque LangGraph store — no DDL of our own
runs against it. ``SqliteSaver.from_conn_string`` yields a context-managed
saver that opens and closes the underlying connection cleanly.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver


@contextmanager
def checkpointer(path: Path) -> Iterator[SqliteSaver]:
    with SqliteSaver.from_conn_string(str(path)) as saver:
        yield saver
