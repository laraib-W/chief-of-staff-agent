"""LLM provider — thin Anthropic SDK wrapper (specs.md §6.3 llm.model)."""

from __future__ import annotations

import anthropic

from app.config.loader import Config


def complete(config: Config, prompt: str, *, max_tokens: int = 200) -> str:
    """Single Anthropic completion call. Raises on failure — callers decide fallback."""
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=config.llm.model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text
