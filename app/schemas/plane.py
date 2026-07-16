"""PlaneIssue and PersonHealth schemas (specs.md §3.1.3, §3.2.3)."""

from pydantic import BaseModel


class PlaneIssue(BaseModel):
    """Single Plane issue. Fields land in the fetch_plane ticket."""


class PersonHealth(BaseModel):
    """Per-person team health card. Fields land in the assess_team ticket."""
