"""CalendarEvent and DayAnalysis schemas (specs.md §3.1.2, §3.2.2)."""

from pydantic import BaseModel


class CalendarEvent(BaseModel):
    """Single calendar event. Fields land in the fetch_calendar ticket."""


class DayAnalysis(BaseModel):
    """Deterministic day summary. Fields land in the analyze_day ticket."""
