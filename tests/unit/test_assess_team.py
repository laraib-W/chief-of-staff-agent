"""Unit tests for the assess_team node (specs.md §3.2.3).

Covers:
- deterministic status tiers (attention / watch / on_track)
- active-only team-average math for the Watch load rule
- on-time-rate honesty rule (only due-dated completions count)
- issue_stuck_since upsert / prune across runs
- injected summarizer path AND fallback path on LLM failure
- unassigned issues are dropped, empty input short-circuits
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.nodes.assess_team import assess_team_node
from app.providers.llm import LLMError
from app.storage import memory as memory_store
from tests.conftest import make_config, make_plane_issue


def _runnable(memory_path):
    return {"configurable": {"memory_db_path": memory_path}}


def _bootstrap_memory(tmp_path):
    path = tmp_path / "memory.db"
    memory_store.bootstrap(path)
    return path


def _no_summary(_cards):
    return {}


@pytest.mark.unit
def test_empty_plane_issues_returns_empty_team_health(tmp_path):
    node = assess_team_node(make_config(), summarize=_no_summary)
    result = node(
        {"plane_issues": [], "errors": {}}, _runnable(_bootstrap_memory(tmp_path))
    )
    assert result == {"team_health": []}


@pytest.mark.unit
def test_coassigned_issue_counts_for_every_assignee(tmp_path):
    """A1 fan-out: one issue on two people appears fully in both cards."""
    issue = make_plane_issue(
        "SHARED-1",
        assignee_ids=["a", "b"],
        assignee_display_names=["Alice", "Bob"],
    )
    node = assess_team_node(make_config(), summarize=_no_summary)
    result = node(
        {"plane_issues": [issue], "errors": {}},
        _runnable(_bootstrap_memory(tmp_path)),
    )
    cards = {c.person_id: c for c in result["team_health"]}
    assert set(cards) == {"a", "b"}
    assert cards["a"].in_progress_count == 1
    assert cards["b"].in_progress_count == 1
    assert cards["a"].person == "Alice"
    assert cards["b"].person == "Bob"


@pytest.mark.unit
def test_unassigned_issue_is_skipped(tmp_path):
    node = assess_team_node(make_config(), summarize=_no_summary)
    result = node(
        {
            "plane_issues": [
                make_plane_issue(
                    issue_id="X-1", assignee_ids=[], assignee_display_names=[]
                ),
            ],
            "errors": {},
        },
        _runnable(_bootstrap_memory(tmp_path)),
    )
    assert result["team_health"] == []


@pytest.mark.unit
def test_clean_person_is_on_track(tmp_path):
    node = assess_team_node(make_config(), summarize=_no_summary)
    result = node(
        {
            "plane_issues": [make_plane_issue(issue_id="P-1")],
            "errors": {},
        },
        _runnable(_bootstrap_memory(tmp_path)),
    )
    (card,) = result["team_health"]
    assert card.status == "on_track"
    assert card.in_progress_count == 1
    assert card.overdue_items == []
    assert card.inactive_items == []


@pytest.mark.unit
def test_overdue_in_progress_is_attention(tmp_path):
    node = assess_team_node(make_config(), summarize=_no_summary)
    result = node(
        {
            "plane_issues": [make_plane_issue(issue_id="P-1", is_overdue=True)],
            "errors": {},
        },
        _runnable(_bootstrap_memory(tmp_path)),
    )
    (card,) = result["team_health"]
    assert card.status == "attention"
    assert [r.issue_id for r in card.overdue_items] == ["P-1"]


@pytest.mark.unit
def test_overdue_backlog_is_attention(tmp_path):
    """Backlog items past their due date trigger attention (nobody picked them up)."""
    node = assess_team_node(make_config(), summarize=_no_summary)
    result = node(
        {
            "plane_issues": [
                make_plane_issue(issue_id="B-1", state_group="backlog", is_overdue=True)
            ],
            "errors": {},
        },
        _runnable(_bootstrap_memory(tmp_path)),
    )
    (card,) = result["team_health"]
    assert card.status == "attention"
    assert [r.issue_id for r in card.overdue_items] == ["B-1"]
    assert card.in_progress_count == 0  # in_progress stays scoped to started


@pytest.mark.unit
def test_overdue_unstarted_is_ignored(tmp_path):
    """'Unstarted' items (e.g. 'Ready') are queued, not overdue — even past due date."""
    node = assess_team_node(make_config(), summarize=_no_summary)
    result = node(
        {
            "plane_issues": [
                make_plane_issue(
                    issue_id="U-1", state_group="unstarted", is_overdue=True
                )
            ],
            "errors": {},
        },
        _runnable(_bootstrap_memory(tmp_path)),
    )
    (card,) = result["team_health"]
    assert card.overdue_items == []
    assert card.status == "on_track"


@pytest.mark.unit
def test_inactive_in_progress_is_attention(tmp_path):
    """age_in_state_days >= config.thresholds.inactivity_days => attention."""
    node = assess_team_node(make_config(), summarize=_no_summary)
    result = node(
        {
            "plane_issues": [make_plane_issue(issue_id="P-1", age_in_state_days=4)],
            "errors": {},
        },
        _runnable(_bootstrap_memory(tmp_path)),
    )
    (card,) = result["team_health"]
    assert card.status == "attention"
    assert [r.issue_id for r in card.inactive_items] == ["P-1"]


@pytest.mark.unit
def test_watch_triggered_by_load_over_active_average(tmp_path):
    """A: 7 in-progress, B: 2, C: 0.

    Active-only average = (7+2)/2 = 4.5. Watch triggers on A when
    in_progress > 1.5 * 4.5 = 6.75, so A qualifies. C has zero in-progress
    and must be excluded from the average.
    """
    issues = (
        [
            make_plane_issue(
                f"A-{i}", assignee_ids=["a"], assignee_display_names=["Alice"]
            )
            for i in range(7)
        ]
        + [
            make_plane_issue(
                f"B-{i}", assignee_ids=["b"], assignee_display_names=["Bob"]
            )
            for i in range(2)
        ]
        + [
            make_plane_issue(
                "C-1",
                assignee_ids=["c"],
                assignee_display_names=["Carol"],
                state_group="completed",
                completed_at=datetime.now(tz=UTC),
            ),
        ]
    )
    node = assess_team_node(make_config(), summarize=_no_summary)
    result = node(
        {"plane_issues": issues, "errors": {}}, _runnable(_bootstrap_memory(tmp_path))
    )
    by_person = {c.person_id: c for c in result["team_health"]}
    assert by_person["a"].status == "watch"
    assert by_person["b"].status == "on_track"
    # Carol has no in-progress work; excluded from the average and not overloaded
    assert by_person["c"].status == "on_track"


@pytest.mark.unit
def test_watch_triggered_by_two_items_due_within_horizon(tmp_path):
    today = datetime.now(tz=UTC).date()
    issues = [
        make_plane_issue("D-1", due_date=today + timedelta(days=2)),
        make_plane_issue("D-2", due_date=today + timedelta(days=5)),
    ]
    node = assess_team_node(make_config(), summarize=_no_summary)
    result = node(
        {"plane_issues": issues, "errors": {}}, _runnable(_bootstrap_memory(tmp_path))
    )
    (card,) = result["team_health"]
    assert card.status == "watch"


@pytest.mark.unit
def test_on_time_rate_ignores_completions_without_due_dates(tmp_path):
    """Completions without a due date only count toward completions_no_due_date."""
    now = datetime.now(tz=UTC)
    issues = [
        # on-time
        make_plane_issue(
            "C-1",
            state_group="completed",
            due_date=now.date(),
            completed_at=now - timedelta(days=1),
        ),
        # late
        make_plane_issue(
            "C-2",
            state_group="completed",
            due_date=(now - timedelta(days=3)).date(),
            completed_at=now - timedelta(days=1),
        ),
        # no due date -> excluded
        make_plane_issue(
            "C-3",
            state_group="completed",
            due_date=None,
            completed_at=now,
        ),
    ]
    node = assess_team_node(make_config(), summarize=_no_summary)
    result = node(
        {"plane_issues": issues, "errors": {}}, _runnable(_bootstrap_memory(tmp_path))
    )
    (card,) = result["team_health"]
    assert card.on_time_rate == pytest.approx(0.5)
    assert card.completions_no_due_date == 1


@pytest.mark.unit
def test_on_time_rate_none_when_no_due_dated_completions(tmp_path):
    node = assess_team_node(make_config(), summarize=_no_summary)
    result = node(
        {
            "plane_issues": [
                make_plane_issue(
                    "N-1",
                    state_group="completed",
                    due_date=None,
                    completed_at=datetime.now(tz=UTC),
                ),
            ],
            "errors": {},
        },
        _runnable(_bootstrap_memory(tmp_path)),
    )
    (card,) = result["team_health"]
    assert card.on_time_rate is None
    assert card.completions_no_due_date == 1


@pytest.mark.unit
def test_days_stuck_populated_on_first_run(tmp_path):
    """First-run inactive items get days_stuck=0 (memory synced before refs built)."""
    node = assess_team_node(make_config(), summarize=_no_summary)
    result = node(
        {
            "plane_issues": [make_plane_issue("S-1", age_in_state_days=5)],
            "errors": {},
        },
        _runnable(_bootstrap_memory(tmp_path)),
    )
    (card,) = result["team_health"]
    (ref,) = card.inactive_items
    assert ref.days_stuck == 0


@pytest.mark.unit
def test_stuck_since_persists_and_bumps_across_runs(tmp_path):
    """First run inserts; second run bumps last_confirmed; recovered issue is pruned."""
    memory_path = _bootstrap_memory(tmp_path)
    node = assess_team_node(make_config(), summarize=_no_summary)

    # Run 1: two issues inactive
    issues1 = [
        make_plane_issue("S-1", age_in_state_days=5),
        make_plane_issue("S-2", age_in_state_days=6),
    ]
    node({"plane_issues": issues1, "errors": {}}, _runnable(memory_path))

    with memory_store.connect(memory_path) as conn:
        row_map_r1 = {
            r["issue_id"]: (r["stuck_since"], r["last_confirmed"])
            for r in conn.execute(
                "SELECT issue_id, stuck_since, last_confirmed FROM issue_stuck_since"
            )
        }
    assert set(row_map_r1) == {"S-1", "S-2"}
    original_stuck_since = row_map_r1["S-1"][0]

    # Run 2: S-1 still inactive, S-2 recovered (no longer inactive)
    issues2 = [
        make_plane_issue("S-1", age_in_state_days=6),
        make_plane_issue("S-2", age_in_state_days=0),  # recovered
    ]
    node({"plane_issues": issues2, "errors": {}}, _runnable(memory_path))

    with memory_store.connect(memory_path) as conn:
        row_map_r2 = {
            r["issue_id"]: (r["stuck_since"], r["last_confirmed"])
            for r in conn.execute(
                "SELECT issue_id, stuck_since, last_confirmed FROM issue_stuck_since"
            )
        }
    assert set(row_map_r2) == {"S-1"}  # S-2 pruned
    assert row_map_r2["S-1"][0] == original_stuck_since  # stuck_since preserved


@pytest.mark.unit
def test_injected_summarizer_populates_summary(tmp_path):
    def fake_summarize(cards):
        return {c.person_id: f"canned line for {c.person}" for c in cards}

    node = assess_team_node(make_config(), summarize=fake_summarize)
    result = node(
        {
            "plane_issues": [
                make_plane_issue(
                    "P-1", assignee_ids=["a"], assignee_display_names=["Alice"]
                )
            ],
            "errors": {},
        },
        _runnable(_bootstrap_memory(tmp_path)),
    )
    (card,) = result["team_health"]
    assert card.summary == "canned line for Alice"


@pytest.mark.unit
def test_summarizer_failure_falls_back_deterministically(tmp_path):
    def flaky(_cards):
        raise LLMError("boom")

    node = assess_team_node(make_config(), summarize=flaky)
    result = node(
        {
            "plane_issues": [make_plane_issue("P-1", is_overdue=True)],
            "errors": {},
        },
        _runnable(_bootstrap_memory(tmp_path)),
    )
    (card,) = result["team_health"]
    # Fallback string mentions the concrete overdue issue ID
    assert "P-1" in card.summary
    assert "overdue" in card.summary
    # Fallback never touches errors dict
    assert result.get("errors", {}) == {}


@pytest.mark.unit
def test_summarizer_retries_once_before_falling_back(tmp_path):
    calls = {"n": 0}

    def flaky(cards):
        calls["n"] += 1
        if calls["n"] == 1:
            raise LLMError("transient")
        return {c.person_id: "ok" for c in cards}

    node = assess_team_node(make_config(), summarize=flaky)
    result = node(
        {
            "plane_issues": [make_plane_issue("P-1")],
            "errors": {},
        },
        _runnable(_bootstrap_memory(tmp_path)),
    )
    (card,) = result["team_health"]
    assert calls["n"] == 2
    assert card.summary == "ok"
