"""LLM provider — thin Anthropic SDK wrapper (specs.md §6.3 llm.model)."""

from __future__ import annotations

import anthropic

from app.config.loader import LLMConfig


def complete(llm_config: LLMConfig, prompt: str, *, max_tokens: int = 200) -> str:
    """Single Anthropic completion call. Raises on failure — callers decide fallback."""
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=llm_config.model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text
