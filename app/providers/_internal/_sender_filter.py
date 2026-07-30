"""Tiered sender filter — allowlist / trusted domains / Tier-3 LLM (specs.md §3.1.1)."""

from __future__ import annotations

from email.utils import parseaddr
from typing import Literal

import structlog

from app.config.loader import GmailConfig, LLMConfig
from app.providers import llm
from app.schemas.email import RawEmail

log = structlog.get_logger(__name__)


def _extract_address(sender: str) -> str:
    """Lowercased email address from a raw From header ('Name <addr>' or 'addr')."""
    _, address = parseaddr(sender)
    return address.lower()


def _domain_of(address: str) -> str:
    return address.rsplit("@", 1)[-1]


def classify_sender(
    address: str, gmail_config: GmailConfig
) -> Literal["allowlist", "trusted_domain", "unknown"]:
    """Tier 1/2 check only. Exact match on address and domain, case-insensitive."""
    address = address.lower()

    allowlist = {a.lower() for a in gmail_config.allowlist}
    if address in allowlist:
        return "allowlist"

    trusted_domains = {d.lower() for d in gmail_config.trusted_domains}
    if _domain_of(address) in trusted_domains:
        return "trusted_domain"

    return "unknown"


def _build_prompt(email: RawEmail) -> str:
    snippet = email.clean_body[:300]
    return (
        "Is this email from a real human with a real ask, as opposed to "
        "automated/marketing/spam content? Answer with just 'yes' or 'no'.\n\n"
        f"From: {email.sender}\nSubject: {email.subject}\nMessage: {snippet}"
    )


def _llm_check_sender(
    llm_config: LLMConfig, email: RawEmail
) -> Literal["llm_trusted", "llm_flagged"]:
    try:
        verdict = llm.complete(llm_config, _build_prompt(email), max_tokens=10)
    except Exception as exc:  # noqa: BLE001 — fail-safe per email, don't abort the run
        log.warning(
            "sender_filter.llm_check_failed", sender=email.sender, error=str(exc)
        )
        return "llm_flagged"

    return "llm_trusted" if "yes" in verdict.strip().lower() else "llm_flagged"


def apply_sender_filter(
    emails: list[RawEmail], gmail_config: GmailConfig, llm_config: LLMConfig
) -> list[RawEmail]:
    """Tag every email's sender_tier. Never removes an email; enforces the LLM cap."""
    cap = gmail_config.max_unknown_sender_llm_calls
    llm_calls_made = 0
    tagged: list[RawEmail] = []

    for email in emails:
        address = _extract_address(email.sender)
        tier = classify_sender(address, gmail_config)

        if tier == "unknown":
            if llm_calls_made < cap:
                llm_calls_made += 1
                tier = _llm_check_sender(llm_config, email)
            else:
                tier = "unchecked"

        tagged.append(email.model_copy(update={"sender_tier": tier}))

    return tagged
