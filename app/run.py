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
from app.logging_config import configure_logging
from app.storage import memory as memory_store
from app.storage import resolve_paths
from app.storage import runs as runs_store
from app.storage.checkpoint import checkpointer

_STATUS_ORDER = {"attention": 0, "watch": 1, "on_track": 2}


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.run")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument(
        "--replay", action="store_true", help="reserved for Phase 0 replay ticket"
    )
    parser.add_argument("--dry-run", action="store_true", help="reserved for §8")
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show DEBUG logs (per-page HTTP traces, etc).",
    )
    args = parser.parse_args()

    configure_logging(verbose=args.verbose)
    config = load_config(args.config)

    paths = resolve_paths(args.data_dir)
    memory_store.bootstrap(paths["memory"])
    runs_store.bootstrap(paths["runs"])

    run_id = runs_store.start_run(paths["runs"], config.config_hash)

    with checkpointer(paths["checkpoints"]) as saver:
        graph = build_graph(config, checkpointer=saver)
        final_state = graph.invoke(
            {"errors": {}},
            config={
                "configurable": {
                    "thread_id": str(run_id),
                    "memory_db_path": paths["memory"],
                }
            },
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
        _print_dry_run(final_state)


def _print_dry_run(final_state) -> None:
    issues = final_state.get("plane_issues", [])
    team_health = final_state.get("team_health", [])
    priorities = final_state.get("top_priorities", [])
    errors = final_state.get("errors", {})

    _print_priorities(priorities)
    _print_per_person(issues)
    _print_errors(errors)
    _print_team_health(team_health)


def _section(title: str, count: int | None = None) -> None:
    header = f"{title} ({count})" if count is not None else title
    print(f"\n{'=' * 70}\n {header}\n{'=' * 70}")


def _print_priorities(priorities) -> None:
    _section("TOP PRIORITIES", len(priorities))
    if not priorities:
        print("  (none — no overdue or inactive items)")
        return
    for p in priorities:
        sources = "+".join(p.source_types)
        print(f"  #{p.rank}  [{sources:<6}]  {p.headline}")
        evidence = ", ".join(f"{e.source}:{e.ref_id}" for e in p.evidence)
        print(f"        evidence: {evidence}")
        if p.connection:
            print(f"        connection: {p.connection}")


def _print_team_health(team_health) -> None:
    _section("TEAM HEALTH", len(team_health))
    if not team_health:
        print("  (no cards)")
        return
    cards = sorted(
        team_health,
        key=lambda c: (_STATUS_ORDER.get(c.status, 99), -c.in_progress_count),
    )
    current_tier: str | None = None
    for card in cards:
        if card.status != current_tier:
            current_tier = card.status
            print(f"\n  --- {current_tier.upper()} ---")
        overdue = f"overdue={len(card.overdue_items)}" if card.overdue_items else ""
        inactive = f"inactive={len(card.inactive_items)}" if card.inactive_items else ""
        flags = "  ".join(x for x in [overdue, inactive] if x)
        line = f"  {card.person:<22}  in_progress={card.in_progress_count:<3}"
        if flags:
            line += f"  {flags}"
        print(line)
        if card.overdue_items:
            ids = ", ".join(r.issue_id for r in card.overdue_items)
            print(f"      overdue:  {ids}")
        if card.inactive_items:
            ids = ", ".join(
                f"{r.issue_id}({r.days_stuck}d)" for r in card.inactive_items
            )
            print(f"      inactive: {ids}")
        if card.summary:
            print(f"      {card.summary}")


def _print_per_person(issues) -> None:
    per_person: dict[str, dict[str, int]] = {}
    for issue in issues:
        for name in issue.assignee_display_names or issue.assignee_ids:
            per_person.setdefault(name, {})[issue.state_group] = (
                per_person.setdefault(name, {}).get(issue.state_group, 0) + 1
            )
    _section("PER-PERSON STATE_GROUP COUNTS", len(per_person))
    if not per_person:
        print("  (no assignees)")
        return
    for name in sorted(per_person):
        breakdown = ", ".join(f"{g}={n}" for g, n in sorted(per_person[name].items()))
        print(f"  {name:<22}  {breakdown}")


def _print_errors(errors) -> None:
    if not errors:
        return
    _section("ERRORS")
    for key, msg in errors.items():
        print(f"  {key}: {msg}")


if __name__ == "__main__":
    main()
