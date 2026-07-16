"""RawEmail and EmailAction schemas (specs.md §3.1.1, §3.2.1)."""

from pydantic import BaseModel


class RawEmail(BaseModel):
    """Sanitized email as it enters agent state. Fields land in fetch_emails."""


class EmailAction(BaseModel):
    """LLM classification output. Fields land in classify_emails."""
