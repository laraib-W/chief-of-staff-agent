"""Unit tests for the correlate node (specs.md §3.3.1).

Covers:
- pre-filter scoring (overdue vs inactive; stuck-days bonus; top-N cap)
- injected correlator path returns ranked priorities
- empty inputs short-circuit
- LLM fallback path: fewer-than-cap priorities get padded from ranked list
- LLM over-cap output gets truncated with ranks renumbered 1..5
- LLM total-failure path: returns deterministic priorities up to the cap
"""

from datetime import date

import pytest

from app.nodes.correlate import correlate_node
from app.providers.llm import LLMError
from app.schemas.digest import EvidenceRef, Priority
from app.schemas.plane import IssueRef, PersonHealth
from tests.conftest import make_config


def _card(
    person_id: str = "u1",
    person: str = "Alice",
    *,
    overdue: list[IssueRef] | None = None,
    inactive: list[IssueRef] | None = None,
) -> PersonHealth:
    return PersonHealth(
        person=person,
        person_id=person_id,
        status="attention",
        in_progress_count=1,
        overdue_items=overdue or [],
        inactive_items=inactive or [],
        on_time_rate=None,
        completions_no_due_date=0,
        last_activity=None,
        summary="",
    )


def _ref(issue_id: str, title: str = "T", *, days_stuck: int | None = None) -> IssueRef:
    return IssueRef(
        issue_id=issue_id,
        title=title,
        due_date=date(2026, 1, 1),
        age_in_state_days=5,
        days_stuck=days_stuck,
    )


@pytest.mark.unit
def test_empty_team_health_returns_empty_priorities():
    node = correlate_node(make_config(), correlate=lambda _r: [])
    result = node({"team_health": [], "errors": {}})
    assert result == {"top_priorities": []}


@pytest.mark.unit
def test_card_with_no_overdue_or_inactive_returns_empty_priorities():
    """On-track people have no candidate items to rank."""
    calls: list[int] = []

    def spy(ranked):
        calls.append(len(ranked))
        return []

    node = correlate_node(make_config(), correlate=spy)
    result = node({"team_health": [_card()], "errors": {}})
    assert result == {"top_priorities": []}
    # short-circuit: correlator never called when nothing to rank
    assert calls == []


@pytest.mark.unit
def test_prefilter_ranks_overdue_above_inactive():
    """Overdue outranks inactive at equal stuck-days."""
    captured: list[str] = []

    def spy(ranked):
        captured.extend(item.issue_ref.issue_id for item in ranked)
        return []

    node = correlate_node(make_config(), correlate=spy)
    node(
        {
            "team_health": [
                _card(overdue=[_ref("O-1")], inactive=[_ref("I-1")]),
            ],
            "errors": {},
        }
    )
    # overdue first (score 3 > inactive's 2)
    assert captured == ["O-1", "I-1"]


@pytest.mark.unit
def test_prefilter_stuck_bonus_promotes_inactive_over_overdue():
    """An inactive-and-long-stuck item (2+1=3) ties overdue-fresh (3), and
    the tiebreaker (days_stuck desc) then puts it first.
    """
    captured: list[str] = []

    def spy(ranked):
        captured.extend(item.issue_ref.issue_id for item in ranked)
        return []

    node = correlate_node(make_config(), correlate=spy)
    node(
        {
            "team_health": [
                _card(
                    overdue=[_ref("OVERDUE", days_stuck=0)],
                    inactive=[_ref("STUCK", days_stuck=12)],
                ),
            ],
            "errors": {},
        }
    )
    assert captured[0] == "STUCK"


@pytest.mark.unit
def test_prefilter_caps_at_top_ten():
    """Only the top 10 items go to the correlator, regardless of source count."""
    captured: list[int] = []

    def spy(ranked):
        captured.append(len(ranked))
        return []

    node = correlate_node(make_config(), correlate=spy)
    node(
        {
            "team_health": [
                _card(overdue=[_ref(f"O-{i}") for i in range(20)]),
            ],
            "errors": {},
        }
    )
    assert captured == [10]


@pytest.mark.unit
def test_llm_output_is_returned_verbatim_when_under_cap():
    """Two LLM priorities → padded to three (one un-covered item remains)."""

    def stub(_ranked):
        return [
            Priority(
                rank=1,
                headline="Two stuck items on Alice",
                evidence=[
                    EvidenceRef(source="plane", ref_id="O-1", label="one"),
                    EvidenceRef(source="plane", ref_id="O-2", label="two"),
                ],
                source_types=["plane"],
                connection="Both blocked on auth review",
            ),
        ]

    node = correlate_node(make_config(), correlate=stub)
    result = node(
        {
            "team_health": [
                _card(
                    overdue=[_ref("O-1"), _ref("O-2"), _ref("O-3")],
                ),
            ],
            "errors": {},
        }
    )
    priorities = result["top_priorities"]
    # 1 LLM + 2 padded (O-3 remains un-covered, others exhausted)
    assert [p.rank for p in priorities] == [1, 2]
    assert priorities[0].headline == "Two stuck items on Alice"
    assert priorities[1].evidence[0].ref_id == "O-3"
    assert priorities[1].connection is None


@pytest.mark.unit
def test_llm_over_cap_is_truncated_and_reranked():
    """Six LLM priorities → truncated to five, ranks renumbered 1..5."""

    def stub(_ranked):
        return [
            Priority(
                rank=99,  # deliberately wrong; must be renumbered
                headline=f"h{i}",
                evidence=[EvidenceRef(source="plane", ref_id=f"X-{i}", label="")],
                source_types=["plane"],
                connection=None,
            )
            for i in range(6)
        ]

    node = correlate_node(make_config(), correlate=stub)
    result = node(
        {
            "team_health": [_card(overdue=[_ref(f"X-{i}") for i in range(6)])],
            "errors": {},
        }
    )
    ranks = [p.rank for p in result["top_priorities"]]
    assert ranks == [1, 2, 3, 4, 5]


@pytest.mark.unit
def test_llm_failure_falls_back_to_deterministic_priorities():
    """Both attempts raise → padder produces up to 5 deterministic entries."""

    def failing(_ranked):
        raise LLMError("boom")

    node = correlate_node(make_config(), correlate=failing)
    result = node(
        {
            "team_health": [
                _card(overdue=[_ref(f"O-{i}", title=f"Title {i}") for i in range(3)]),
            ],
            "errors": {},
        }
    )
    priorities = result["top_priorities"]
    assert [p.rank for p in priorities] == [1, 2, 3]
    for p in priorities:
        assert p.source_types == ["plane"]
        assert p.connection is None
        assert len(p.evidence) == 1
        assert p.evidence[0].source == "plane"
