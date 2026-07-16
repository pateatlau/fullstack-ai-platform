import asyncio
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from anthropic import APITimeoutError as AnthropicAPITimeoutError
from httpx import Request as HTTPXRequest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.providers.anthropic_provider import ANTHROPIC_MAX_TOKENS, AnthropicProvider
from app.providers.base import ProviderChunk
from app.schemas.chat import ChatMessageSchema


class _FakeTextBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _FakeToolBlock:
    def __init__(self) -> None:
        self.type = "tool_use"


class _FakeMessageResponse:
    def __init__(self, content: list[Any]) -> None:
        self.content = content


class _FakeMessagesApi:
    def __init__(self, response: _FakeMessageResponse) -> None:
        self._response = response
        self.last_kwargs: dict[str, Any] | None = None

    async def create(self, **kwargs: Any) -> _FakeMessageResponse:
        self.last_kwargs = kwargs
        return self._response

    def stream(self, **kwargs: Any) -> Any:
        self.last_kwargs = kwargs
        return _FakeStreamManager(kwargs.get("events", []))


class _RaisingMessagesApi(_FakeMessagesApi):
    def __init__(self, exc: Exception) -> None:
        super().__init__(_FakeMessageResponse([]))
        self._exc = exc

    async def create(self, **kwargs: Any) -> _FakeMessageResponse:
        self.last_kwargs = kwargs
        raise self._exc

    def stream(self, **kwargs: Any) -> Any:
        self.last_kwargs = kwargs
        return _RaisingStreamManager(self._exc)


class _FakeStreamManager:
    def __init__(self, events: list[Any]) -> None:
        self._events = events

    async def __aenter__(self) -> "_FakeStreamIterator":
        return _FakeStreamIterator(self._events)

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class _RaisingStreamManager:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def __aenter__(self) -> Any:
        raise self._exc

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


class _FakeStreamIterator:
    def __init__(self, events: list[Any]) -> None:
        self._events = events

    def __aiter__(self) -> "_FakeStreamIterator":
        return self

    async def __anext__(self) -> Any:
        if not self._events:
            raise StopAsyncIteration
        return self._events.pop(0)


class _FakeClient:
    def __init__(self, messages_api: _FakeMessagesApi) -> None:
        self.messages = messages_api


class _FakeTextDelta:
    def __init__(self, text: str) -> None:
        self.type = "text_delta"
        self.text = text


class _FakeContentBlockDeltaEvent:
    def __init__(self, text: str) -> None:
        self.type = "content_block_delta"
        self.delta = _FakeTextDelta(text)


class _FakeNonTextDelta:
    def __init__(self) -> None:
        self.type = "input_json_delta"
        self.partial_json = "{}"


class _FakeNonTextContentBlockDeltaEvent:
    def __init__(self) -> None:
        self.type = "content_block_delta"
        self.delta = _FakeNonTextDelta()


class _FakeMessageDelta:
    def __init__(self, stop_reason: str | None) -> None:
        self.stop_reason = stop_reason


class _FakeMessageDeltaEvent:
    def __init__(self, stop_reason: str | None) -> None:
        self.type = "message_delta"
        self.delta = _FakeMessageDelta(stop_reason)


class _FakeMessageStopEvent:
    def __init__(self) -> None:
        self.type = "message_stop"


def test_complete_chat_maps_system_to_top_level_system_field() -> None:
    fake_messages_api = _FakeMessagesApi(
        _FakeMessageResponse([_FakeTextBlock("Anthropic reply")])
    )

    provider = AnthropicProvider(api_key="test-key")
    cast(Any, provider)._client = _FakeClient(fake_messages_api)

    result = asyncio.run(
        provider.complete_chat(
            messages=[
                ChatMessageSchema(role="system", content="You are concise."),
                ChatMessageSchema(role="user", content="Hello"),
                ChatMessageSchema(role="assistant", content="Hi"),
            ],
            model="claude-haiku-4-5-20251001",
            temperature=0.5,
        )
    )

    assert result == "Anthropic reply"
    assert fake_messages_api.last_kwargs is not None
    assert fake_messages_api.last_kwargs["model"] == "claude-haiku-4-5-20251001"
    assert fake_messages_api.last_kwargs["temperature"] == 0.5
    assert fake_messages_api.last_kwargs["max_tokens"] == ANTHROPIC_MAX_TOKENS
    assert fake_messages_api.last_kwargs["system"] == "You are concise."
    assert fake_messages_api.last_kwargs["messages"] == [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi"},
    ]


def test_complete_chat_joins_multiple_system_messages() -> None:
    fake_messages_api = _FakeMessagesApi(_FakeMessageResponse([_FakeTextBlock("OK")]))

    provider = AnthropicProvider(api_key="test-key")
    cast(Any, provider)._client = _FakeClient(fake_messages_api)

    asyncio.run(
        provider.complete_chat(
            messages=[
                ChatMessageSchema(role="system", content="Rule 1"),
                ChatMessageSchema(role="system", content="Rule 2"),
                ChatMessageSchema(role="user", content="Hello"),
            ],
            model="claude-haiku-4-5-20251001",
        )
    )

    assert fake_messages_api.last_kwargs is not None
    assert fake_messages_api.last_kwargs["system"] == "Rule 1\n\nRule 2"


def test_complete_chat_extracts_only_text_blocks() -> None:
    fake_messages_api = _FakeMessagesApi(
        _FakeMessageResponse(
            [
                _FakeTextBlock("Anthropic "),
                _FakeToolBlock(),
                _FakeTextBlock("text"),
            ]
        )
    )

    provider = AnthropicProvider(api_key="test-key")
    cast(Any, provider)._client = _FakeClient(fake_messages_api)

    result = asyncio.run(
        provider.complete_chat(
            messages=[ChatMessageSchema(role="user", content="Hello")],
            model="claude-haiku-4-5-20251001",
        )
    )

    assert result == "Anthropic text"
    assert fake_messages_api.last_kwargs is not None
    assert "system" not in fake_messages_api.last_kwargs


def test_complete_chat_propagates_sdk_timeout_error() -> None:
    provider = AnthropicProvider(api_key="test-key")
    cast(Any, provider)._client = _FakeClient(
        _RaisingMessagesApi(
            AnthropicAPITimeoutError(
                request=HTTPXRequest("POST", "https://example.test")
            )
        )
    )

    with pytest.raises(AnthropicAPITimeoutError):
        asyncio.run(
            provider.complete_chat(
                messages=[ChatMessageSchema(role="user", content="Hello")],
                model="claude-haiku-4-5-20251001",
            )
        )


def test_stream_chat_yields_text_deltas_and_stop_reason() -> None:
    fake_messages_api = _FakeMessagesApi(_FakeMessageResponse([]))

    def _stream(**kwargs: Any) -> _FakeStreamManager:
        fake_messages_api.last_kwargs = kwargs
        return _FakeStreamManager(
            [
                _FakeContentBlockDeltaEvent("Anthropic "),
                _FakeContentBlockDeltaEvent("stream"),
                _FakeMessageDeltaEvent("end_turn"),
            ]
        )

    fake_messages_api.stream = _stream  # type: ignore[assignment]

    provider = AnthropicProvider(api_key="test-key")
    provider._client = _FakeClient(fake_messages_api)  # type: ignore[assignment]

    async def gather_chunks() -> list[ProviderChunk]:
        chunks: list[ProviderChunk] = []
        async for chunk in provider.stream_chat(
            messages=[ChatMessageSchema(role="user", content="Hello")],
            model="claude-haiku-4-5-20251001",
            temperature=0.6,
        ):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(gather_chunks())

    assert fake_messages_api.last_kwargs is not None
    assert fake_messages_api.last_kwargs["model"] == "claude-haiku-4-5-20251001"
    assert fake_messages_api.last_kwargs["temperature"] == 0.6
    assert fake_messages_api.last_kwargs["max_tokens"] == ANTHROPIC_MAX_TOKENS
    assert chunks == [
        {"content": "Anthropic ", "finish_reason": None},
        {"content": "stream", "finish_reason": None},
        {"content": "", "finish_reason": "end_turn"},
    ]


def test_stream_chat_ignores_non_text_metadata_events() -> None:
    fake_messages_api = _FakeMessagesApi(_FakeMessageResponse([]))

    def _stream(**kwargs: Any) -> _FakeStreamManager:
        fake_messages_api.last_kwargs = kwargs
        return _FakeStreamManager(
            [
                _FakeNonTextContentBlockDeltaEvent(),
                _FakeMessageStopEvent(),
                _FakeContentBlockDeltaEvent("payload"),
            ]
        )

    fake_messages_api.stream = _stream  # type: ignore[assignment]

    provider = AnthropicProvider(api_key="test-key")
    cast(Any, provider)._client = _FakeClient(fake_messages_api)

    async def gather_chunks() -> list[ProviderChunk]:
        chunks: list[ProviderChunk] = []
        async for chunk in provider.stream_chat(
            messages=[ChatMessageSchema(role="user", content="Hello")],
            model="claude-haiku-4-5-20251001",
        ):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(gather_chunks())

    assert chunks == [{"content": "payload", "finish_reason": None}]


def test_stream_chat_propagates_sdk_timeout_error() -> None:
    provider = AnthropicProvider(api_key="test-key")
    cast(Any, provider)._client = _FakeClient(
        _RaisingMessagesApi(
            AnthropicAPITimeoutError(
                request=HTTPXRequest("POST", "https://example.test")
            )
        )
    )

    async def gather_chunks() -> list[ProviderChunk]:
        chunks: list[ProviderChunk] = []
        with pytest.raises(AnthropicAPITimeoutError):
            async for chunk in provider.stream_chat(
                messages=[ChatMessageSchema(role="user", content="Hello")],
                model="claude-haiku-4-5-20251001",
            ):
                chunks.append(chunk)
        return chunks

    asyncio.run(gather_chunks())
