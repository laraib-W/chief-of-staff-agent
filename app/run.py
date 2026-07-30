"""CLI entry point for a morning run (specs.md §8).

Phase 0 wiring: load config, bootstrap the three SQLite files, build the
(currently one-node) graph, invoke it, and audit the run. Real sensor/judgment
work lands as each Phase 1+ ticket replaces the placeholder node in
``app.graph.workflow``.
"""

# Bootstrap order matters: load .env first so COS_FORCE_IPV4 (and future
# env-driven toggles) can live in .env alongside secrets; then import
# app.net_compat so its side-effect patch fires before any HTTP library
# resolves a hostname. isort/ruff would otherwise reorder these.
from dotenv import load_dotenv  # isort: skip

load_dotenv()

import app.net_compat  # noqa: F401, E402  # isort: skip  # pyright: ignore[reportUnusedImport]

import argparse  # noqa: E402
from pathlib import Path  # noqa: E402

from app.config.loader import load_config  # noqa: E402
from app.graph.workflow import build_graph  # noqa: E402
from app.logging_config import configure_logging  # noqa: E402
from app.schemas.plane import PERSON_STATUS_ORDER  # noqa: E402
from app.storage import memory as memory_store  # noqa: E402
from app.storage import resolve_paths  # noqa: E402
from app.storage import runs as runs_store  # noqa: E402
from app.storage.checkpoint import checkpointer  # noqa: E402


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
    emails = final_state.get("emails", [])
    calendar_events = final_state.get("calendar_events", [])
    errors = final_state.get("errors", {})

    _print_priorities(priorities)
    _print_emails(emails)
    _print_calendar(calendar_events)
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
        key=lambda c: (PERSON_STATUS_ORDER.get(c.status, 99), -c.in_progress_count),
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


def _print_emails(emails) -> None:
    _section("EMAILS", len(emails))
    if not emails:
        print("  (none)")
        return
    for e in sorted(emails, key=lambda x: x.date, reverse=True):
        when = e.date.strftime("%Y-%m-%d %H:%M")
        subject = e.subject or "(no subject)"
        print(f"  [{when}]  {e.sender}")
        print(f"      {subject}")


def _print_calendar(events) -> None:
    _section("CALENDAR", len(events))
    if not events:
        print("  (none)")
        return
    by_scope: dict[str, list] = {"today": [], "lookahead": []}
    for ev in events:
        by_scope.setdefault(ev.scope, []).append(ev)
    for scope in ("today", "lookahead"):
        scope_events = sorted(by_scope.get(scope, []), key=lambda x: x.start)
        if not scope_events:
            continue
        print(f"\n  --- {scope.upper()} ---")
        for ev in scope_events:
            if ev.all_day:
                when = ev.start.strftime("%Y-%m-%d (all-day)")
            else:
                when = (
                    f"{ev.start.strftime('%Y-%m-%d %H:%M')}–{ev.end.strftime('%H:%M')}"
                )
            flags = []
            if ev.is_pending_invite:
                flags.append("pending")
            if ev.response_status and ev.response_status != "accepted":
                flags.append(ev.response_status)
            flag_str = f"  [{', '.join(flags)}]" if flags else ""
            print(f"  {when}  {ev.summary}{flag_str}")
            if ev.attendees:
                shown = ", ".join(ev.attendees[:3])
                more = "..." if len(ev.attendees) > 3 else ""
                print(f"      attendees: {len(ev.attendees)} ({shown}{more})")
            if scope == "today" and ev.location:
                print(f"      location: {ev.location}")


def _print_errors(errors) -> None:
    if not errors:
        return
    _section("ERRORS")
    for key, msg in errors.items():
        print(f"  {key}: {msg}")


if __name__ == "__main__":
    main()
