"""LLM provider — multi-backend wrapper (specs.md §6.3 llm.provider/model).

Callers own the retry policy and the JSON schema — this module only wraps
the transport. Each backend raises a single ``LLMError`` so callers don't
need to import provider-specific exceptions.

Add a new backend by implementing ``_Provider`` and wiring it into
``_build_provider``.
"""

import os
import time
from typing import Protocol

import anthropic
import structlog
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from app.config.loader import LLMConfig

log = structlog.get_logger(__name__)

# Cap every LLM request at this many seconds. When exceeded, the SDK raises
# and _safe_summarize / _safe_correlate flip to the deterministic fallback.
# 20s is chosen so a slow-but-successful call still lands, but a stuck
# thinking loop on reasoning-heavy prompts (correlate) trips the fallback
# fast enough that a morning digest still lands in reasonable time. Raise
# it if you switch to a slower model tier or start batching more work.
_HTTP_TIMEOUT_SECONDS = 20


class LLMError(Exception):
    """Any failure talking to the LLM (auth, transport, empty response)."""


class _Provider(Protocol):
    def complete_json(self, *, system: str, user: str) -> str: ...


class _AnthropicProvider:
    def __init__(self, model: str, max_tokens: int, api_key: str | None) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self._client: anthropic.Anthropic | None = None

    def _get_client(self) -> anthropic.Anthropic:
        if self._client is None:
            if not self._api_key:
                raise LLMError("ANTHROPIC_API_KEY is not set")
            self._client = anthropic.Anthropic(
                api_key=self._api_key,
                timeout=_HTTP_TIMEOUT_SECONDS,
            )
        return self._client

    def complete_json(self, *, system: str, user: str) -> str:
        log.info(
            "llm_call_started",
            provider="anthropic",
            model=self._model,
            input_chars=len(system) + len(user),
        )
        started = time.monotonic()
        try:
            response = self._get_client().messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except anthropic.APIError as exc:
            log.warning(
                "llm_call_failed",
                provider="anthropic",
                model=self._model,
                duration_ms=int((time.monotonic() - started) * 1000),
                reason=str(exc),
            )
            raise LLMError(str(exc)) from exc

        duration_ms = int((time.monotonic() - started) * 1000)
        if not response.content:
            log.warning(
                "llm_call_empty",
                provider="anthropic",
                model=self._model,
                duration_ms=duration_ms,
            )
            raise LLMError("empty response from Anthropic")
        block = response.content[0]
        if not hasattr(block, "text") or not block.text:
            raise LLMError("first content block has no text")
        log.info(
            "llm_call_done",
            provider="anthropic",
            model=self._model,
            duration_ms=duration_ms,
            output_chars=len(block.text),
        )
        return block.text


class _GeminiProvider:
    def __init__(self, model: str, max_tokens: int, api_key: str | None) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._api_key = api_key or os.getenv("GEMINI_API_KEY")
        self._client: genai.Client | None = None

    def _get_client(self) -> genai.Client:
        if self._client is None:
            if not self._api_key:
                raise LLMError("GEMINI_API_KEY is not set")
            self._client = genai.Client(
                api_key=self._api_key,
                http_options=genai_types.HttpOptions(
                    timeout=_HTTP_TIMEOUT_SECONDS * 1000,  # milliseconds
                ),
            )
        return self._client

    def complete_json(self, *, system: str, user: str) -> str:
        log.info(
            "llm_call_started",
            provider="gemini",
            model=self._model,
            input_chars=len(system) + len(user),
        )
        started = time.monotonic()
        try:
            response = self._get_client().models.generate_content(
                model=self._model,
                contents=user,
                config=genai_types.GenerateContentConfig(
                    system_instruction=system,
                    max_output_tokens=self._max_tokens,
                    # Cap "thinking" tokens to the smallest value the newer
                    # lite models accept. Our prompts are structured JSON
                    # extraction, not reasoning — a tiny budget lets the
                    # model skip the thinking pass in practice (observed
                    # totalTokenCount matches candidatesTokenCount) without
                    # tripping the 400 that thinking_budget=0 raises on
                    # gemini-3.x-lite. -1 would enable dynamic thinking and
                    # add measurable latency on our batched prompts.
                    thinking_config=genai_types.ThinkingConfig(thinking_budget=128),
                ),
            )
        except genai_errors.APIError as exc:
            log.warning(
                "llm_call_failed",
                provider="gemini",
                model=self._model,
                duration_ms=int((time.monotonic() - started) * 1000),
                reason=str(exc),
            )
            raise LLMError(str(exc)) from exc

        duration_ms = int((time.monotonic() - started) * 1000)
        text = response.text
        if not text:
            log.warning(
                "llm_call_empty",
                provider="gemini",
                model=self._model,
                duration_ms=duration_ms,
            )
            raise LLMError("empty response from Gemini")
        log.info(
            "llm_call_done",
            provider="gemini",
            model=self._model,
            duration_ms=duration_ms,
            output_chars=len(text),
        )
        return text


def _build_provider(config: LLMConfig, api_key: str | None) -> _Provider:
    if config.provider == "anthropic":
        return _AnthropicProvider(config.model, config.max_tokens_per_node, api_key)
    if config.provider == "gemini":
        return _GeminiProvider(config.model, config.max_tokens_per_node, api_key)
    raise LLMError(f"unknown llm.provider: {config.provider!r}")


class LLMClient:
    """Facade over the configured backend. Prefer this over instantiating
    a provider directly — callers stay portable across backends.
    """

    def __init__(self, config: LLMConfig, api_key: str | None = None) -> None:
        self._provider = _build_provider(config, api_key)

    def complete_json(self, *, system: str, user: str) -> str:
        """Send a single request and return the raw text content.

        The prompt is responsible for asking for JSON; the caller is
        responsible for parsing and validating it. Empty responses raise
        ``LLMError`` so callers can distinguish "no content" from bad JSON.
        Markdown code fences are stripped — Gemini in particular wraps
        JSON in ```json ... ``` even when asked not to, and forcing the
        model into strict-JSON output mode makes it noticeably slower.
        """
        return _strip_code_fences(
            self._provider.complete_json(system=system, user=user)
        )


def _strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines[-1].strip() == "```":
        lines = lines[:-1]
    first = lines[0].lstrip("`").strip()
    if first in ("", "json"):
        lines = lines[1:]
    return "\n".join(lines).strip()
