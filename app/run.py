"""CLI entry point for a morning run (specs.md §8).

Phase 0 wiring: load config, bootstrap the three SQLite files, build the
(currently one-node) graph, invoke it, and audit the run. Real sensor/judgment
work lands as each Phase 1+ ticket replaces the placeholder node in
``app.graph.workflow``.
"""

import argparse
from pathlib import Path

from app.config.loader import load_config
from app.graph.workflow import build_graph
from app.storage import memory as memory_store
from app.storage import resolve_paths
from app.storage import runs as runs_store
from app.storage.checkpoint import checkpointer


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.run")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument(
        "--replay", action="store_true", help="reserved for Phase 0 replay ticket"
    )
    parser.add_argument("--dry-run", action="store_true", help="reserved for §8")
    args = parser.parse_args()

    config = load_config(args.config)

    paths = resolve_paths(args.data_dir)
    memory_store.bootstrap(paths["memory"])
    runs_store.bootstrap(paths["runs"])

    run_id = runs_store.start_run(paths["runs"], config.config_hash)

    with checkpointer(paths["checkpoints"]) as saver:
        graph = build_graph(config, checkpointer=saver)
        final_state = graph.invoke(
            {"errors": {}},
            config={"configurable": {"thread_id": str(run_id), "agent_config": config}},
        )

    runs_store.finish_run(
        paths["runs"],
        run_id=run_id,
        node_durations={},
        total_tokens=0,
        errors=final_state.get("errors", {}),
    )

    print(f"run {run_id} ok (config_hash={config.config_hash[:12]})")

    if args.dry_run:
        issues = final_state.get("plane_issues", [])
        errors = final_state.get("errors", {})
        print(f"\n--- plane_issues ({len(issues)}) ---")
        for issue in issues:
            overdue = " [OVERDUE]" if issue.is_overdue else ""
            module = f"[{issue.module_name}] " if issue.module_name else ""
            print(
                f"  {issue.issue_id}  {issue.state_name:<14}"
                f"  {issue.assignee_display_name or '(unassigned)':<20}"
                f"  {module}{issue.title}{overdue}"
            )
        if errors:
            print("\n--- errors ---")
            for key, msg in errors.items():
                print(f"  {key}: {msg}")


if __name__ == "__main__":
    main()
