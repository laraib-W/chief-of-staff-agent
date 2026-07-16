"""Priority schema and top-level AgentState TypedDict (specs.md §3.3.1, §4)."""

from typing import TypedDict

from pydantic import BaseModel

from app.schemas.calendar import CalendarEvent, DayAnalysis
from app.schemas.email import EmailAction, RawEmail
from app.schemas.plane import PersonHealth, PlaneIssue


class Priority(BaseModel):
    """Correlated cross-source priority. Fields land in the correlate ticket."""


class AgentState(TypedDict, total=False):
    emails: list[RawEmail]
    calendar_events: list[CalendarEvent]
    plane_issues: list[PlaneIssue]

    email_actions: list[EmailAction]
    day_analysis: DayAnalysis
    team_health: list[PersonHealth]

    top_priorities: list[Priority]

    errors: dict[str, str | None]
    digest_html: str
