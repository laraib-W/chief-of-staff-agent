"""Priority schema and top-level AgentState TypedDict (specs.md §3.3.1, §4)."""

import operator
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel

from app.schemas.calendar import CalendarEvent, DayAnalysis
from app.schemas.email import EmailAction, RawEmail
from app.schemas.plane import PersonHealth, PlaneIssue

SourceType = Literal["plane", "email", "calendar"]


class EvidenceRef(BaseModel):
    """Pointer to the concrete item a priority is grounded in."""

    source: SourceType
    ref_id: str  # issue_id / message_id / event_id — must exist in the source data
    label: str  # short human-readable title of the referenced item


class Priority(BaseModel):
    """Correlated cross-source priority (specs.md §3.3.1)."""

    rank: int  # 1..5, 1 is highest
    headline: str  # one-line summary, observation-only
    evidence: list[EvidenceRef]
    source_types: list[SourceType]  # which sources contributed
    connection: str | None = None  # cross-source narrative when applicable


class AgentState(TypedDict, total=False):
    emails: list[RawEmail]
    calendar_events: list[CalendarEvent]
    plane_issues: list[PlaneIssue]

    email_actions: list[EmailAction]
    day_analysis: DayAnalysis
    team_health: list[PersonHealth]

    top_priorities: list[Priority]

    # Multiple sensor nodes write their per-sensor status here in parallel;
    # dict-union reducer lets LangGraph merge those writes without collision.
    errors: Annotated[dict[str, str | None], operator.or_]
    digest_html: str
