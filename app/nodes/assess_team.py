"""assess_team rules+LLM node (specs.md §3.2.3).

Deterministic per-person numbers and status tier from ``plane_issues``.
An injectable ``summarize`` callable writes the one-line human summary so
tests can bypass the network. When the summarizer fails, a deterministic
fallback sentence is used and the digest still ships (specs §9).
"""

import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path

import structlog
from langgraph.types import RunnableConfig
from pydantic import BaseModel, TypeAdapter, ValidationError

from app.config.loader import Config, ThresholdsConfig
from app.providers.llm import LLMClient, LLMError
from app.schemas.digest import AgentState
from app.schemas.plane import IssueRef, PersonHealth, PersonStatus, PlaneIssue
from app.storage.memory import get_all_stuck, prune_stuck, upsert_stuck

log = structlog.get_logger(__name__)

_IN_PROGRESS_GROUP = "started"
_COMPLETED_GROUP = "completed"
# Overdue includes started work and backlog items whose deadline slipped
# before anyone picked them up. 'unstarted' (e.g., "Ready") is excluded on
# purpose — those items are queued to be worked, not lost.
_OVERDUE_GROUPS = ("backlog", _IN_PROGRESS_GROUP)

_SUMMARY_SYSTEM = (
    "You write one-line status sentences for a team lead's morning digest. "
    "Rules: exactly one sentence per person; work-item-centric, never "
    "person-judging; reference specific issue IDs from the data you're "
    "given; do not invent numbers or IDs; return strict JSON of the shape "
    '{"summaries": [{"person_id": "...", "summary": "..."}]}.'
)


class _SummaryItem(BaseModel):
    person_id: str
    summary: str


class _SummaryPayload(BaseModel):
    summaries: list[_SummaryItem]


_PAYLOAD_ADAPTER = TypeAdapter(_SummaryPayload)


SummarizerInput = list[PersonHealth]
Summarizer = Callable[[SummarizerInput], dict[str, str]]


def assess_team_node(
    agent_config: Config,
    summarize: Summarizer | None = None,
) -> Callable[[AgentState, RunnableConfig], dict]:
    """Build the assess_team node bound to a loaded Config.

    ``summarize`` maps person_id -> one-line summary. When omitted, a
    default binding uses ``LLMClient`` under the hood; tests inject a fake.
    """
    summarizer = summarize or _default_summarizer(agent_config)
    thresholds = agent_config.thresholds

    def _node(state: AgentState, config: RunnableConfig) -> dict:
        memory_db_path: Path = config["configurable"]["memory_db_path"]
        issues = state.get("plane_issues") or []
        if not issues:
            log.info("assess_team.ok", people=0, reason="no_issues")
            return {"team_health": []}

        today = datetime.now(tz=UTC).date()
        grouped = _group_by_assignee(issues)

        current_inactive_ids = {
            issue.issue_id
            for person_issues in grouped.values()
            for issue in person_issues
            if _is_inactive(issue, thresholds.inactivity_days)
        }
        for issue_id in current_inactive_ids:
            upsert_stuck(memory_db_path, issue_id, today)
        prune_stuck(memory_db_path, current_inactive_ids)
        stuck_lookup = get_all_stuck(memory_db_path)

        cards: list[PersonHealth] = []
        in_progress_by_person: dict[str, list[PlaneIssue]] = {}
        for person_id, person_issues in grouped.items():
            card, in_progress = _build_person_card(
                person_id=person_id,
                issues=person_issues,
                inactivity_days=thresholds.inactivity_days,
                stuck_lookup=stuck_lookup,
                today=today,
            )
            cards.append(card)
            in_progress_by_person[person_id] = in_progress

        active_counts = [
            card.in_progress_count for card in cards if card.in_progress_count > 0
        ]
        team_avg = sum(active_counts) / len(active_counts) if active_counts else 0.0

        for card in cards:
            card.status = _classify(
                card,
                in_progress_by_person[card.person_id],
                team_avg,
                thresholds,
                today,
            )

        summaries = _safe_summarize(summarizer, cards)
        for card in cards:
            card.summary = summaries.get(card.person_id) or _fallback_summary(card)

        log.info("assess_team.ok", people=len(cards))
        return {"team_health": cards}

    return _node


def _is_inactive(issue: PlaneIssue, inactivity_days: int) -> bool:
    return (
        issue.state_group == _IN_PROGRESS_GROUP
        and issue.age_in_state_days >= inactivity_days
    )


def _default_summarizer(config: Config) -> Summarizer:
    client = LLMClient(config.llm)

    def _summarize(cards: SummarizerInput) -> dict[str, str]:
        prompt_payload = [
            {
                "person_id": card.person_id,
                "person": card.person,
                "status": card.status,
                "in_progress_count": card.in_progress_count,
                "overdue": [
                    {"issue_id": r.issue_id, "title": r.title}
                    for r in card.overdue_items
                ],
                "inactive": [
                    {
                        "issue_id": r.issue_id,
                        "title": r.title,
                        "days_stuck": r.days_stuck,
                    }
                    for r in card.inactive_items
                ],
                "on_time_rate": card.on_time_rate,
                "completions_no_due_date": card.completions_no_due_date,
            }
            for card in cards
        ]
        user = (
            "Write one sentence per person using the numbers and issue IDs "
            "below. Do not add, invent, or omit any issue ID.\n\n"
            + json.dumps({"people": prompt_payload}, default=str)
        )
        log.info("assess_team.llm_call_started", people=len(cards))
        raw = client.complete_json(system=_SUMMARY_SYSTEM, user=user)
        payload = _PAYLOAD_ADAPTER.validate_json(raw)
        return {item.person_id: item.summary for item in payload.summaries}

    return _summarize


def _safe_summarize(
    summarizer: Summarizer, cards: list[PersonHealth]
) -> dict[str, str]:
    """Call the summarizer with one retry; on failure return {} and log."""
    if not cards:
        return {}
    for attempt in (1, 2):
        try:
            return summarizer(cards)
        except (LLMError, ValidationError, ValueError) as exc:
            log.warning("assess_team.llm_fallback", attempt=attempt, reason=str(exc))
    return {}


def _group_by_assignee(issues: list[PlaneIssue]) -> dict[str, list[PlaneIssue]]:
    """Fan an issue across all of its assignees (A1 semantics).

    A co-assigned issue appears in every assignee's list — each person is
    credited full weight for work they are carrying, even if not primary.
    Consequences the caller should know: (a) totals across cards will
    double-count co-assigned issues, and (b) an inactive co-assigned issue
    contributes to every co-assignee's attention tier.
    """
    grouped: dict[str, list[PlaneIssue]] = {}
    for issue in issues:
        for assignee_id in issue.assignee_ids:
            grouped.setdefault(assignee_id, []).append(issue)
    return grouped


def _resolve_display_name(person_id: str, issues: list[PlaneIssue]) -> str:
    """Pick the display name matching person_id across parallel assignee lists.

    Falls back to person_id (UUID) when no matching name exists — happens
    when a member is missing from ``list_members`` but still appears as an
    assignee on an issue.
    """
    for issue in issues:
        for pid, name in zip(
            issue.assignee_ids, issue.assignee_display_names, strict=False
        ):
            if pid == person_id and name:
                return name
    return person_id


def _build_person_card(
    *,
    person_id: str,
    issues: list[PlaneIssue],
    inactivity_days: int,
    stuck_lookup: dict[str, date],
    today: date,
) -> tuple[PersonHealth, list[PlaneIssue]]:
    """Return the pre-classification card and the raw in-progress issues.

    The raw list is handed to ``_classify`` so the "due within horizon"
    Watch rule can see every in-progress issue, not just the overdue /
    inactive subset that ends up on the card. Overdue spans every active
    state (backlog / unstarted / started) via ``is_overdue``; inactive is
    scoped to in-progress work since backlog items haven't been started.
    """
    display_name = _resolve_display_name(person_id, issues)

    in_progress = [i for i in issues if i.state_group == _IN_PROGRESS_GROUP]
    overdue = [i for i in issues if i.is_overdue and i.state_group in _OVERDUE_GROUPS]
    inactive = [i for i in in_progress if i.age_in_state_days >= inactivity_days]

    completed = [i for i in issues if i.state_group == _COMPLETED_GROUP]
    completed_with_due = [
        i for i in completed if i.due_date is not None and i.completed_at is not None
    ]
    completions_no_due_date = sum(1 for i in completed if i.due_date is None)
    on_time_rate = (
        sum(1 for i in completed_with_due if i.completed_at.date() <= i.due_date)
        / len(completed_with_due)
        if completed_with_due
        else None
    )

    updates = [i.updated_at for i in issues if i.updated_at is not None]
    last_activity = max(updates) if updates else None

    card = PersonHealth(
        person=display_name,
        person_id=person_id,
        status="on_track",  # rewritten in _classify once team_avg is known
        in_progress_count=len(in_progress),
        overdue_items=[_to_ref(i, stuck_lookup, today) for i in overdue],
        inactive_items=[_to_ref(i, stuck_lookup, today) for i in inactive],
        on_time_rate=on_time_rate,
        completions_no_due_date=completions_no_due_date,
        last_activity=last_activity,
        summary="",  # filled in after summarizer runs
    )
    return card, in_progress


def _to_ref(issue: PlaneIssue, stuck_lookup: dict[str, date], today: date) -> IssueRef:
    stuck_since = stuck_lookup.get(issue.issue_id)
    days_stuck = max(0, (today - stuck_since).days) if stuck_since is not None else None
    return IssueRef(
        issue_id=issue.issue_id,
        title=issue.title,
        due_date=issue.due_date,
        age_in_state_days=issue.age_in_state_days,
        days_stuck=days_stuck,
    )


def _classify(
    card: PersonHealth,
    in_progress: list[PlaneIssue],
    team_avg: float,
    thresholds: ThresholdsConfig,
    today: date,
) -> PersonStatus:
    if card.overdue_items or card.inactive_items:
        return "attention"
    load_over = (
        team_avg > 0 and card.in_progress_count > thresholds.load_multiplier * team_avg
    )
    due_soon = sum(
        1
        for issue in in_progress
        if issue.due_date is not None
        and 0 <= (issue.due_date - today).days <= thresholds.due_soon_horizon_days
    )
    if load_over or due_soon >= 2:
        return "watch"
    return "on_track"


def _fallback_summary(card: PersonHealth) -> str:
    parts = [f"{card.in_progress_count} in progress"]
    if card.overdue_items:
        ids = ", ".join(r.issue_id for r in card.overdue_items[:3])
        parts.append(f"{len(card.overdue_items)} overdue ({ids})")
    if card.inactive_items:
        ids = ", ".join(r.issue_id for r in card.inactive_items[:3])
        parts.append(f"{len(card.inactive_items)} inactive ({ids})")
    return "; ".join(parts) + "."
