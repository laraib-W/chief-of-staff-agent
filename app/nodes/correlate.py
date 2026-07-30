"""correlate cross-source LLM node (specs.md §3.3.1).

Turns per-source health cards into a ranked list of at most 5 priorities.
The pipeline: deterministic pre-filter (top ~10 by urgency) → LLM writes
headlines and cross-source narrative → pad or truncate to the hard cap.

Only ``team_health`` is populated today. The node handles email_actions
and day_analysis being empty gracefully — when they arrive later, the
pre-filter and prompt already know how to include them.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import structlog
from pydantic import BaseModel, TypeAdapter, ValidationError

from app.config.loader import Config
from app.providers.llm import LLMClient, LLMError
from app.schemas.digest import AgentState, EvidenceRef, Priority
from app.schemas.plane import IssueRef, PersonHealth

log = structlog.get_logger(__name__)

_OVERDUE_SCORE = 3
_INACTIVE_SCORE = 2
_STUCK_LONG_BONUS = 1
_STUCK_LONG_DAYS = 7
_PREFILTER_TOP_N = 10
_HARD_CAP = 5
_HEADLINE_MAX_CHARS = 140

_CATEGORY = Literal["overdue", "inactive"]

_SUMMARY_SYSTEM = (
    "You produce a ranked list of at most 5 priorities for a team lead's "
    "morning digest. Rules: rank 1 is most important; each priority has a "
    "one-line headline; evidence must reference the exact ref_ids from the "
    "data given; do not invent items or IDs; observation-only, never "
    "recommend an action. Return strict JSON of the shape "
    '{"priorities": [{"rank": 1, "headline": "...", "evidence": '
    '[{"source": "plane", "ref_id": "...", "label": "..."}], '
    '"source_types": ["plane"], "connection": null}]}.'
)


class _PriorityPayload(BaseModel):
    priorities: list[Priority]


_PAYLOAD_ADAPTER = TypeAdapter(_PriorityPayload)


@dataclass(frozen=True)
class _RankedItem:
    """A pre-filtered Plane candidate carrying enough context for prompts."""

    score: int
    person_id: str
    person_name: str
    issue_ref: IssueRef
    category: _CATEGORY


CorrelatorInput = list[_RankedItem]
Correlator = Callable[[CorrelatorInput], list[Priority]]


def correlate_node(
    agent_config: Config,
    correlate: Correlator | None = None,
) -> Callable[[AgentState], dict]:
    """Build the correlate node bound to a loaded Config.

    ``correlate`` maps the pre-filtered candidates to a list of Priority
    objects. When omitted, a default binding uses ``LLMClient``; tests
    inject a fake.
    """
    correlator = correlate or _default_correlator(agent_config)

    def _node(state: AgentState) -> dict:
        team_health = state.get("team_health") or []
        ranked = _prefilter_plane(team_health)

        if not ranked:
            # Email + calendar aren't wired yet; when they land, extend the
            # pre-filter and remove this early return.
            log.info("correlate.ok", priorities=0, reason="no_ranked_items")
            return {"top_priorities": []}

        llm_priorities = _safe_correlate(correlator, ranked)
        priorities = _pad_or_truncate(llm_priorities, ranked)
        log.info(
            "correlate.ok",
            priorities=len(priorities),
            source_llm=len(llm_priorities),
            padded=len(priorities) - len(llm_priorities),
        )
        return {"top_priorities": priorities}

    return _node


def _prefilter_plane(team_health: list[PersonHealth]) -> list[_RankedItem]:
    """Score every overdue / inactive item and keep the top N by urgency.

    Score: overdue=3, inactive=2, +1 if the item has been stuck ≥7 days.
    Ties break by days_stuck descending so long-standing items surface
    first within the same tier.
    """
    ranked: list[_RankedItem] = []
    for card in team_health:
        for issue_ref in card.overdue_items:
            ranked.append(
                _RankedItem(
                    score=_score(issue_ref, _OVERDUE_SCORE),
                    person_id=card.person_id,
                    person_name=card.person,
                    issue_ref=issue_ref,
                    category="overdue",
                )
            )
        for issue_ref in card.inactive_items:
            ranked.append(
                _RankedItem(
                    score=_score(issue_ref, _INACTIVE_SCORE),
                    person_id=card.person_id,
                    person_name=card.person,
                    issue_ref=issue_ref,
                    category="inactive",
                )
            )
    ranked.sort(key=lambda x: (-x.score, -(x.issue_ref.days_stuck or 0)))
    return ranked[:_PREFILTER_TOP_N]


def _score(issue_ref: IssueRef, base: int) -> int:
    if issue_ref.days_stuck is not None and issue_ref.days_stuck >= _STUCK_LONG_DAYS:
        return base + _STUCK_LONG_BONUS
    return base


def _default_correlator(config: Config) -> Correlator:
    client = LLMClient(config.llm)

    def _correlate(ranked: CorrelatorInput) -> list[Priority]:
        prompt_payload = [
            {
                "person": item.person_name,
                "person_id": item.person_id,
                "issue_id": item.issue_ref.issue_id,
                "title": item.issue_ref.title,
                "category": item.category,
                "days_stuck": item.issue_ref.days_stuck,
                "due_date": item.issue_ref.due_date,
                "score": item.score,
            }
            for item in ranked
        ]
        user = (
            "Correlate the items below into at most 5 ranked priorities. "
            "Every priority's evidence must reference an issue_id from the "
            "list. Prefer priorities that connect multiple items over "
            "one-item entries when a real connection exists.\n\n"
            + json.dumps({"plane_items": prompt_payload}, default=str)
        )
        log.info("correlate.llm_call_started", ranked_items=len(ranked))
        raw = client.complete_json(system=_SUMMARY_SYSTEM, user=user)
        payload = _PAYLOAD_ADAPTER.validate_json(raw)
        return payload.priorities

    return _correlate


def _safe_correlate(
    correlator: Correlator, ranked: list[_RankedItem]
) -> list[Priority]:
    """One retry, then return []; the caller pads from the pre-filtered list."""
    for attempt in (1, 2):
        try:
            return correlator(ranked)
        except (LLMError, ValidationError, ValueError) as exc:
            log.warning("correlate.llm_fallback", attempt=attempt, reason=str(exc))
    return []


def _pad_or_truncate(
    llm_priorities: list[Priority], ranked: list[_RankedItem]
) -> list[Priority]:
    """Enforce the hard cap; pad with un-covered items when the LLM under-produces.

    Truncation renumbers ranks so the returned list is always contiguously
    1..N. Padded items reference a single Plane issue with no cross-source
    connection — they're deterministic filler, not correlations.
    """
    if not llm_priorities and not ranked:
        return []

    if len(llm_priorities) >= _HARD_CAP:
        return [
            p.model_copy(update={"rank": i + 1})
            for i, p in enumerate(llm_priorities[:_HARD_CAP])
        ]

    covered_refs = {ev.ref_id for p in llm_priorities for ev in p.evidence}
    padded: list[Priority] = [
        p.model_copy(update={"rank": i + 1}) for i, p in enumerate(llm_priorities)
    ]
    next_rank = len(padded) + 1
    for item in ranked:
        if next_rank > _HARD_CAP:
            break
        if item.issue_ref.issue_id in covered_refs:
            continue
        padded.append(_deterministic_priority(next_rank, item))
        next_rank += 1
    return padded


def _deterministic_priority(rank: int, item: _RankedItem) -> Priority:
    headline = (
        f"{item.person_name}: {item.issue_ref.issue_id} "
        f"{item.issue_ref.title} ({item.category})"
    )
    return Priority(
        rank=rank,
        headline=headline[:_HEADLINE_MAX_CHARS],
        evidence=[
            EvidenceRef(
                source="plane",
                ref_id=item.issue_ref.issue_id,
                label=item.issue_ref.title,
            )
        ],
        source_types=["plane"],
        connection=None,
    )
