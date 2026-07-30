"""PlaneIssue and PersonHealth schemas (specs.md §3.1.3, §3.2.3)."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

StateGroup = Literal["backlog", "unstarted", "started", "completed", "cancelled"]


class PlaneIssue(BaseModel):
    issue_id: str  # human-readable: e.g. "PROJ-42"
    project_id: str
    title: str
    state_name: str  # e.g. "In Progress"
    state_group: (
        StateGroup | str
    )  # Literal covers standard Plane groups; str allows custom workspaces
    # Every assignee attached to the issue. Empty list = unassigned.
    # Parallel to assignee_display_names by index. assess_team fans the
    # issue across all assignees so co-assigned work is counted for each
    # person carrying it.
    assignee_ids: list[str]
    assignee_display_names: list[str]
    due_date: date | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    module_name: str | None
    age_in_state_days: int  # days since the issue entered its current state
    is_overdue: bool  # due_date < today and state_group not in completed/cancelled


PersonStatus = Literal["attention", "watch", "on_track"]

# Urgency ordering: lower value = higher urgency. Use for sorting team-health
# cards so "attention" surfaces first, "on_track" last.
PERSON_STATUS_ORDER: dict[PersonStatus, int] = {
    "attention": 0,
    "watch": 1,
    "on_track": 2,
}


class IssueRef(BaseModel):
    """Compact reference to a PlaneIssue for use inside PersonHealth."""

    issue_id: str  # human-readable, e.g. "PROJ-142"
    title: str
    due_date: date | None = None
    age_in_state_days: int = 0
    days_stuck: int | None = None  # populated from issue_stuck_since memory


class PersonHealth(BaseModel):
    """Per-person team health card (specs.md §3.2.3).

    Numbers are always deterministic. ``summary`` is the LLM-generated
    one-liner (or a deterministic fallback when the LLM path fails).
    """

    person: str  # display_name
    person_id: str  # Plane assignee_id (UUID)
    status: PersonStatus
    in_progress_count: int
    overdue_items: list[IssueRef]
    inactive_items: list[IssueRef]
    on_time_rate: float | None  # None when no due-dated completions in window
    completions_no_due_date: int
    last_activity: datetime | None
    summary: str
