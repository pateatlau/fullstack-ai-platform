"""TracingLLMProvider span and lifecycle tests."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.ai.observability.tracing.provider import TracerRegistry
from app.ai.observability.tracing.provider_wrapper import TracingLLMProvider
from app.core.config import Settings
from app.providers.base import (
    ChatMessageInput,
    ProviderChunk,
    ProviderCompletion,
    ProviderToolCall,
    ProviderToolCompletion,
    ProviderUsage,
)
from app.providers.factory import ProviderFactory
from app.schemas.chat import ChatMessageSchema
from tests.fakes import FakeProvider

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _reset_tracer_registry() -> Iterator[None]:
    TracerRegistry.reset_for_tests()
    yield
    TracerRegistry.reset_for_tests()


@pytest.fixture
def memory_exporter() -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    settings = Settings(openai_api_key="test-key", observability_enabled=True)
    TracerRegistry.initialize(
        settings,
        extra_span_processors=[SimpleSpanProcessor(exporter)],
    )
    return exporter


class StreamingUsageProvider(FakeProvider):
    """Fake provider that attaches usage only on the terminal stream chunk."""

    def __init__(
        self,
        *,
        chunk_delay_seconds: float = 0.05,
        intermediate_usage: ProviderUsage | None = ProviderUsage(
            prompt_tokens=1, completion_tokens=1, total_tokens=2
        ),
        terminal_usage: ProviderUsage | None = ProviderUsage(
            prompt_tokens=10, completion_tokens=5, total_tokens=15
        ),
    ) -> None:
        super().__init__(response="one two three")
        self.chunk_delay_seconds = chunk_delay_seconds
        self.intermediate_usage = intermediate_usage
        self.terminal_usage = terminal_usage

    async def stream_chat(
        self,
        messages: list[ChatMessageSchema],
        model: str,
        temperature: float = 0.7,
        *,
        max_tokens: int | None = None,
    ) -> AsyncIterator[ProviderChunk]:
        del messages, model, temperature, max_tokens
        words = self.response.split(" ")
        for index, word in enumerate(words):
            await asyncio.sleep(self.chunk_delay_seconds)
            is_last = index == len(words) - 1
            chunk: ProviderChunk = {
                "content": word if is_last else f"{word} ",
                "finish_reason": "stop" if is_last else None,
            }
            if is_last:
                if self.terminal_usage is not None:
                    chunk["usage"] = self.terminal_usage
            elif self.intermediate_usage is not None:
                chunk["usage"] = self.intermediate_usage
            yield chunk


async def test_complete_chat_emits_llm_span_with_token_attributes(
    memory_exporter: InMemorySpanExporter,
) -> None:
    inner = FakeProvider(
        response="hello",
        usage=ProviderUsage(prompt_tokens=12, completion_tokens=8, total_tokens=20),
    )
    provider = TracingLLMProvider(inner, "openai")

    result = await provider.complete_chat(
        [ChatMessageSchema(role="user", content="hi")],
        "gpt-4o-mini",
    )

    assert result.content == "hello"
    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "llm.complete"
    attributes = dict(spans[0].attributes or {})
    assert attributes["provider"] == "openai"
    assert attributes["model"] == "gpt-4o-mini"
    assert attributes["streaming"] is False
    assert attributes["prompt_tokens"] == 12
    assert attributes["completion_tokens"] == 8
    assert attributes["total_tokens"] == 20
    assert attributes["finish_reason"] == "stop"
    assert isinstance(attributes["latency_ms"], int)
    assert attributes["latency_ms"] >= 0


async def test_complete_chat_with_tools_emits_llm_span(
    memory_exporter: InMemorySpanExporter,
) -> None:
    inner = FakeProvider(
        tool_completions=[
            ProviderToolCompletion(
                content=None,
                tool_calls=[
                    ProviderToolCall(id="tc1", name="search", arguments={"q": "x"})
                ],
                finish_reason="tool_calls",
                usage=ProviderUsage(
                    prompt_tokens=3, completion_tokens=2, total_tokens=5
                ),
            )
        ]
    )
    provider = TracingLLMProvider(inner, "anthropic")

    result = await provider.complete_chat_with_tools(
        [ChatMessageSchema(role="user", content="find it")],
        "claude-3-5-sonnet",
        tools=[{"type": "function", "function": {"name": "search"}}],
    )

    assert result.finish_reason == "tool_calls"
    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 1
    attributes = dict(spans[0].attributes or {})
    assert attributes["provider"] == "anthropic"
    assert attributes["model"] == "claude-3-5-sonnet"
    assert attributes["prompt_tokens"] == 3
    assert attributes["completion_tokens"] == 2
    assert attributes["total_tokens"] == 5


async def test_stream_chat_emits_one_span_covering_full_stream(
    memory_exporter: InMemorySpanExporter,
) -> None:
    inner = StreamingUsageProvider(chunk_delay_seconds=0.08)
    provider = TracingLLMProvider(inner, "groq")
    messages = [ChatMessageSchema(role="user", content="stream")]

    stream_start = time.perf_counter()
    chunks: list[ProviderChunk] = []
    async for chunk in provider.stream_chat(messages, "llama-3.1-70b"):
        chunks.append(chunk)
    stream_elapsed_ms = int((time.perf_counter() - stream_start) * 1000)

    assert len(chunks) == 3
    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "llm.complete"
    attributes = dict(spans[0].attributes or {})
    assert attributes["streaming"] is True
    assert attributes["prompt_tokens"] == 10
    assert attributes["completion_tokens"] == 5
    assert attributes["total_tokens"] == 15
    assert attributes["finish_reason"] == "stop"
    assert spans[0].start_time is not None
    assert spans[0].end_time is not None
    span_duration_ms = (spans[0].end_time - spans[0].start_time) / 1_000_000
    assert span_duration_ms >= stream_elapsed_ms - 20


async def test_stream_chat_ignores_intermediate_usage_on_span(
    memory_exporter: InMemorySpanExporter,
) -> None:
    inner = StreamingUsageProvider(
        intermediate_usage=ProviderUsage(
            prompt_tokens=99, completion_tokens=99, total_tokens=198
        ),
        terminal_usage=ProviderUsage(
            prompt_tokens=4, completion_tokens=2, total_tokens=6
        ),
    )
    provider = TracingLLMProvider(inner, "openai")
    messages = [ChatMessageSchema(role="user", content="stream")]

    async for _chunk in provider.stream_chat(messages, "gpt-4o-mini"):
        pass

    attributes = dict(memory_exporter.get_finished_spans()[0].attributes or {})
    assert attributes["prompt_tokens"] == 4
    assert attributes["completion_tokens"] == 2
    assert attributes["total_tokens"] == 6


async def test_tracing_provider_does_not_persist_usage(
    memory_exporter: InMemorySpanExporter,
) -> None:
    inner = FakeProvider()
    provider = TracingLLMProvider(inner, "openai")
    messages = [ChatMessageSchema(role="user", content="hi")]

    with patch("app.db.usage.SqlUsageStore.record", new_callable=AsyncMock) as record:
        await provider.complete_chat(messages, "gpt-4o-mini")
        record.assert_not_called()


def test_provider_factory_wraps_when_observability_enabled() -> None:
    settings = Settings(openai_api_key="test-key", observability_enabled=True)
    provider = ProviderFactory.get_provider("openai", settings)
    assert isinstance(provider, TracingLLMProvider)


def test_provider_factory_returns_concrete_provider_when_disabled() -> None:
    settings = Settings(openai_api_key="test-key", observability_enabled=False)
    provider = ProviderFactory.get_provider("openai", settings)
    assert type(provider).__name__ == "OpenAIProvider"


async def test_llm_telemetry_failure_is_fail_open(
    memory_exporter: InMemorySpanExporter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    inner = FakeProvider()
    provider = TracingLLMProvider(inner, "openai")
    broken_tracer = MagicMock()
    broken_tracer.start_span.side_effect = RuntimeError("telemetry down")

    with patch(
        "app.ai.observability.tracing.spans.get_tracer",
        return_value=broken_tracer,
    ):
        result = await provider.complete_chat(
            [ChatMessageSchema(role="user", content="hi")],
            "gpt-4o-mini",
        )

    assert result.content == inner.response
    assert any(
        "Observability span setup failed" in record.message for record in caplog.records
    )
    assert len(memory_exporter.get_finished_spans()) == 0


async def test_llm_business_failure_propagates(
    memory_exporter: InMemorySpanExporter,
) -> None:
    class BoomProvider:
        async def complete_chat(
            self,
            messages: list[ChatMessageSchema],
            model: str,
            temperature: float = 0.7,
            *,
            max_tokens: int | None = None,
        ) -> ProviderCompletion:
            del messages, model, temperature, max_tokens
            raise ValueError("provider exploded")

        async def complete_chat_with_tools(
            self,
            messages: list[ChatMessageInput],
            model: str,
            tools: list[dict[str, object]],
            temperature: float = 0.7,
            *,
            max_tokens: int | None = None,
        ) -> ProviderToolCompletion:
            del messages, model, tools, temperature, max_tokens
            raise ValueError("provider exploded")

        async def stream_chat(
            self,
            messages: list[ChatMessageSchema],
            model: str,
            temperature: float = 0.7,
            *,
            max_tokens: int | None = None,
        ) -> AsyncIterator[ProviderChunk]:
            del messages, model, temperature, max_tokens
            if False:  # pragma: no cover - async generator marker
                yield ProviderChunk(content="", finish_reason=None)
            raise ValueError("provider exploded")

    provider = TracingLLMProvider(BoomProvider(), "openai")

    with pytest.raises(ValueError, match="provider exploded"):
        await provider.complete_chat(
            [ChatMessageSchema(role="user", content="hi")],
            "gpt-4o-mini",
        )

    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].status.status_code.name == "ERROR"
