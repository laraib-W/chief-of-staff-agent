"""CalendarEvent and DayAnalysis schemas (specs.md §3.1.2, §3.2.2)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class CalendarEvent(BaseModel):
    """Single calendar event as it enters agent state.

    ``scope="today"`` events carry full detail (location, description, organizer).
    ``scope="lookahead"`` events carry title / time / attendees only,
    per specs.md §3.1.2.
    """

    id: str
    summary: str
    start: datetime
    end: datetime
    all_day: bool
    attendees: list[str]
    response_status: str | None
    is_pending_invite: bool
    scope: Literal["today", "lookahead"]
    location: str | None = None
    description: str | None = None
    organizer: str | None = None


class DayAnalysis(BaseModel):
    """Deterministic day summary. Fields land in the analyze_day ticket."""
