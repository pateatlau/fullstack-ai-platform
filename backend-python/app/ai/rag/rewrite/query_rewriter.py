"""LLM-backed query rewriter for advanced RAG (Phase 5).

Uses :class:`~app.providers.base.LLMProvider` + :class:`~app.ai.prompts.manager.PromptManager`
only — no provider SDK imports. Failures fall back to the original query.
"""

from __future__ import annotations

import time
import uuid
from typing import Literal, cast

from app.ai.prompts.manager import PromptManager
from app.core.config import Settings
from app.core.logging import get_logger
from app.providers.base import LLMProvider
from app.schemas.chat import ChatMessageSchema, ProviderName

_logger = get_logger(__name__)

_REWRITE_TEMPERATURE = 0.0
_REWRITE_MAX_TOKENS = 128
_PROMPT_CATEGORY = "rag"
_PROMPT_NAME = "query_rewrite"
_PROMPT_VERSION = "1"


class LLMQueryRewriter:
    """Rewrite a user question into a retrieval query via the configured LLM.

    On provider/prompt failure or empty output, returns the original query and
    logs ``query_rewrite_failed`` without raw question/completion text.
    """

    def __init__(
        self,
        *,
        provider: LLMProvider,
        prompt_manager: PromptManager,
        settings: Settings,
    ) -> None:
        self._provider = provider
        self._prompt_manager = prompt_manager
        self._settings = settings

    async def rewrite(self, query: str, *, user_id: uuid.UUID) -> str:
        _ = user_id  # reserved for future owner-scoped logging / personalization
        original = query
        if not original.strip():
            return original

        start = time.perf_counter()
        try:
            prompt = self._prompt_manager.render(
                _PROMPT_CATEGORY,
                _PROMPT_NAME,
                _PROMPT_VERSION,
                {"question": original},
            )
            messages = [ChatMessageSchema.model_construct(role="user", content=prompt)]
            model = self._default_model()
            completion = await self._provider.complete_chat(
                messages,
                model,
                _REWRITE_TEMPERATURE,
                max_tokens=_REWRITE_MAX_TOKENS,
            )
            rewritten = _normalize_rewritten_query(completion.content)
            if not rewritten:
                _log_rewrite(
                    start,
                    failed=True,
                    reason="empty_output",
                )
                return original
            _log_rewrite(start, failed=False)
            return rewritten
        except Exception:
            _log_rewrite(start, failed=True, reason="exception")
            return original

    def _default_model(self) -> str:
        provider_name_raw = self._settings.llm_provider
        allowed: tuple[ProviderName, ...] = (
            "openai",
            "gemini",
            "groq",
            "anthropic",
        )
        if provider_name_raw not in allowed:
            raise ValueError(
                f"Unsupported LLM_PROVIDER '{provider_name_raw}' for query rewrite."
            )
        provider_name = cast(ProviderName, provider_name_raw)
        defaults: dict[ProviderName, str] = {
            "openai": self._settings.openai_model,
            "gemini": self._settings.gemini_model,
            "groq": self._settings.groq_model,
            "anthropic": self._settings.anthropic_model,
        }
        return defaults[provider_name]


def _normalize_rewritten_query(content: str | None) -> str:
    """Strip formatting noise; keep the first non-empty line only."""
    if content is None:
        return ""
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        return _strip_wrapping_quotes(stripped)
    return ""


def _strip_wrapping_quotes(text: str) -> str:
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"', "`"}:
        return text[1:-1].strip()
    return text


def _log_rewrite(
    start: float,
    *,
    failed: bool,
    reason: Literal["empty_output", "exception"] | None = None,
) -> None:
    latency_ms = int((time.perf_counter() - start) * 1000)
    if failed:
        _logger.warning(
            "Query rewrite failed; using original query",
            query_rewrite_latency_ms=latency_ms,
            query_rewrite_failed=True,
            query_rewrite_failure_reason=reason,
        )
    else:
        _logger.info(
            "Query rewrite completed",
            query_rewrite_latency_ms=latency_ms,
            query_rewrite_failed=False,
        )
