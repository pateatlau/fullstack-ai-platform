"""TracingLLMProvider decorator — LLM span coverage without adapter changes."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

from opentelemetry.trace import Span

from app.ai.observability.metrics.instruments import record_llm_request_metrics
from app.ai.observability.tracing.spans import _set_span_attributes, llm_span
from app.providers.base import (
    ChatMessageInput,
    LLMProvider,
    ProviderChunk,
    ProviderCompletion,
    ProviderToolCompletion,
    ProviderUsage,
)
from app.schemas.chat import ChatMessageSchema


def _record_llm_completion_attributes(
    span: Span | None,
    *,
    provider: str,
    model: str,
    finish_reason: str | None,
    usage: ProviderUsage | None,
    latency_ms: int,
    succeeded: bool = True,
) -> None:
    record_llm_request_metrics(
        provider=provider,
        model=model,
        succeeded=succeeded,
        total_tokens=usage.total_tokens if usage is not None else None,
    )
    if span is None:
        return
    attributes: dict[str, Any] = {
        "latency_ms": latency_ms,
        "finish_reason": finish_reason,
    }
    if usage is not None:
        attributes["prompt_tokens"] = usage.prompt_tokens
        attributes["completion_tokens"] = usage.completion_tokens
        attributes["total_tokens"] = usage.total_tokens
    _set_span_attributes(span, attributes)


class TracingLLMProvider:
    """Decorator over ``LLMProvider`` that emits ``llm.complete`` spans only."""

    def __init__(self, inner: LLMProvider, provider_name: str) -> None:
        self._inner = inner
        self._provider_name = provider_name

    async def complete_chat(
        self,
        messages: list[ChatMessageSchema],
        model: str,
        temperature: float = 0.7,
        *,
        max_tokens: int | None = None,
    ) -> ProviderCompletion:
        start = time.perf_counter()
        with llm_span(self._provider_name, model, streaming=False) as span:
            result = await self._inner.complete_chat(
                messages,
                model,
                temperature,
                max_tokens=max_tokens,
            )
            latency_ms = int((time.perf_counter() - start) * 1000)
            _record_llm_completion_attributes(
                span,
                provider=self._provider_name,
                model=model,
                finish_reason=result.finish_reason,
                usage=result.usage,
                latency_ms=latency_ms,
            )
            return result

    async def complete_chat_with_tools(
        self,
        messages: list[ChatMessageInput],
        model: str,
        tools: list[dict[str, object]],
        temperature: float = 0.7,
        *,
        max_tokens: int | None = None,
    ) -> ProviderToolCompletion:
        start = time.perf_counter()
        with llm_span(self._provider_name, model, streaming=False) as span:
            result = await self._inner.complete_chat_with_tools(
                messages,
                model,
                tools,
                temperature,
                max_tokens=max_tokens,
            )
            latency_ms = int((time.perf_counter() - start) * 1000)
            _record_llm_completion_attributes(
                span,
                provider=self._provider_name,
                model=model,
                finish_reason=result.finish_reason,
                usage=result.usage,
                latency_ms=latency_ms,
            )
            return result

    async def stream_chat(
        self,
        messages: list[ChatMessageSchema],
        model: str,
        temperature: float = 0.7,
        *,
        max_tokens: int | None = None,
    ) -> AsyncIterator[ProviderChunk]:
        start = time.perf_counter()
        with llm_span(self._provider_name, model, streaming=True) as span:
            terminal_finish_reason: str | None = None
            terminal_usage: ProviderUsage | None = None
            async for chunk in self._inner.stream_chat(
                messages,
                model,
                temperature,
                max_tokens=max_tokens,
            ):
                if chunk.get("finish_reason"):
                    terminal_finish_reason = chunk["finish_reason"]
                usage = chunk.get("usage")
                if usage is not None:
                    terminal_usage = usage
                yield chunk
            latency_ms = int((time.perf_counter() - start) * 1000)
            _record_llm_completion_attributes(
                span,
                provider=self._provider_name,
                model=model,
                finish_reason=terminal_finish_reason,
                usage=terminal_usage,
                latency_ms=latency_ms,
            )
