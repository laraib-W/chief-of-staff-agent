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
    assignee_id: str | None
    assignee_display_name: str | None
    due_date: date | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    module_name: str | None
    age_in_state_days: int  # days since the issue entered its current state
    is_overdue: bool  # due_date < today and state_group not in completed/cancelled


class PersonHealth(BaseModel):
    """Per-person team health card. Fields land in the assess_team ticket."""
