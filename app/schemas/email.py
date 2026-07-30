"""RawEmail and EmailAction schemas (specs.md §3.1.1, §3.2.1)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

SenderTier = Literal[
    "allowlist",
    "trusted_domain",
    "llm_trusted",
    "llm_flagged",
    "unchecked",
]


class RawEmail(BaseModel):
    """Sanitized email as it enters agent state. Fields land in fetch_emails."""

    id: str
    thread_id: str
    sender: str
    subject: str
    clean_body: str
    date: datetime
    sender_tier: SenderTier = "unchecked"


class EmailAction(BaseModel):
    """LLM classification output. Fields land in classify_emails."""
