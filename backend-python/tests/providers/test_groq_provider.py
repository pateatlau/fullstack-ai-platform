import asyncio
import sys
from pathlib import Path
from typing import Any, AsyncIterator

import pytest
from httpx import Request as HTTPXRequest
from groq import APITimeoutError as GroqAPITimeoutError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.providers.base import ProviderChunk
from app.providers.groq_provider import GroqProvider
from app.schemas.chat import ChatMessageSchema


class _FakeMessage:
    def __init__(self, content: Any) -> None:
        self.content = content


class _FakeChoice:
    def __init__(self, content: Any) -> None:
        self.message = _FakeMessage(content)


class _FakeDelta:
    def __init__(self, content: str | None) -> None:
        self.content = content


class _FakeStreamChoice:
    def __init__(self, content: str | None, finish_reason: str | None) -> None:
        self.delta = _FakeDelta(content)
        self.finish_reason = finish_reason


class _FakeStreamEvent:
    def __init__(self, choices: list[_FakeStreamChoice]) -> None:
        self.choices = choices


class _FakeResponse:
    def __init__(self, choices: list[_FakeChoice]) -> None:
        self.choices = choices


class _FakeCompletions:
    def __init__(
        self, response: _FakeResponse, stream_events: list[_FakeStreamEvent]
    ) -> None:
        self._response = response
        self._stream_events = stream_events
        self.last_kwargs: dict[str, Any] | None = None

    async def create(
        self, **kwargs: Any
    ) -> _FakeResponse | AsyncIterator[_FakeStreamEvent]:
        self.last_kwargs = kwargs
        if kwargs.get("stream"):

            async def _iterate() -> AsyncIterator[_FakeStreamEvent]:
                for event in self._stream_events:
                    yield event

            return _iterate()
        return self._response


class _RaisingCompletions:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc
        self.last_kwargs: dict[str, Any] | None = None

    async def create(self, **kwargs: Any) -> Any:
        self.last_kwargs = kwargs
        raise self.exc


class _FakeChat:
    def __init__(self, completions: Any) -> None:
        self.completions = completions


class _FakeClient:
    def __init__(self, completions: Any) -> None:
        self.chat = _FakeChat(completions)


class _Part:
    def __init__(self, text: str) -> None:
        self.text = text


def test_complete_chat_maps_messages_and_returns_text() -> None:
    response = _FakeResponse([_FakeChoice("Groq full response")])
    fake_completions = _FakeCompletions(response, stream_events=[])

    provider = GroqProvider(api_key="test-key")
    provider._client = _FakeClient(fake_completions)  # type: ignore[assignment]

    result = asyncio.run(
        provider.complete_chat(
            messages=[
                ChatMessageSchema(role="system", content="You are concise."),
                ChatMessageSchema(role="user", content="hello"),
            ],
            model="openai/gpt-oss-20b",
            temperature=0.4,
        )
    )

    assert result == "Groq full response"
    assert fake_completions.last_kwargs is not None
    assert fake_completions.last_kwargs["model"] == "openai/gpt-oss-20b"
    assert fake_completions.last_kwargs["temperature"] == 0.4
    assert fake_completions.last_kwargs["stream"] is False
    assert fake_completions.last_kwargs["messages"] == [
        {"role": "system", "content": "You are concise."},
        {"role": "user", "content": "hello"},
    ]


def test_complete_chat_coerces_structured_message_content() -> None:
    response = _FakeResponse([_FakeChoice([_Part("Groq "), _Part("structured")])])
    fake_completions = _FakeCompletions(response, stream_events=[])

    provider = GroqProvider(api_key="test-key")
    provider._client = _FakeClient(fake_completions)  # type: ignore[assignment]

    result = asyncio.run(
        provider.complete_chat(
            messages=[ChatMessageSchema(role="user", content="hello")],
            model="openai/gpt-oss-20b",
            temperature=0.7,
        )
    )

    assert result == "Groq structured"


def test_complete_chat_returns_empty_string_when_choices_missing() -> None:
    response = _FakeResponse([])
    fake_completions = _FakeCompletions(response, stream_events=[])

    provider = GroqProvider(api_key="test-key")
    provider._client = _FakeClient(fake_completions)  # type: ignore[assignment]

    result = asyncio.run(
        provider.complete_chat(
            messages=[ChatMessageSchema(role="user", content="hello")],
            model="openai/gpt-oss-20b",
        )
    )

    assert result == ""


def test_complete_chat_propagates_sdk_timeout_error() -> None:
    provider = GroqProvider(api_key="test-key")
    provider._client = _FakeClient(  # type: ignore[assignment]
        _RaisingCompletions(
            GroqAPITimeoutError(request=HTTPXRequest("POST", "https://example.test"))
        )
    )

    with pytest.raises(GroqAPITimeoutError):
        asyncio.run(
            provider.complete_chat(
                messages=[ChatMessageSchema(role="user", content="hello")],
                model="openai/gpt-oss-20b",
            )
        )


def test_stream_chat_yields_content_and_finish_reason() -> None:
    response = _FakeResponse([])
    stream_events = [
        _FakeStreamEvent([_FakeStreamChoice("Groq ", None)]),
        _FakeStreamEvent([_FakeStreamChoice("stream", None)]),
        _FakeStreamEvent([_FakeStreamChoice(None, "stop")]),
    ]
    fake_completions = _FakeCompletions(response, stream_events=stream_events)

    provider = GroqProvider(api_key="test-key")
    provider._client = _FakeClient(fake_completions)  # type: ignore[assignment]

    async def gather_chunks() -> list[ProviderChunk]:
        chunks: list[ProviderChunk] = []
        async for chunk in provider.stream_chat(
            messages=[ChatMessageSchema(role="user", content="hello")],
            model="openai/gpt-oss-20b",
            temperature=0.3,
        ):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(gather_chunks())

    assert fake_completions.last_kwargs is not None
    assert fake_completions.last_kwargs["stream"] is True
    assert chunks == [
        {"content": "Groq ", "finish_reason": None},
        {"content": "stream", "finish_reason": None},
        {"content": "", "finish_reason": "stop"},
    ]


def test_stream_chat_skips_empty_non_terminal_chunks() -> None:
    response = _FakeResponse([])
    stream_events = [
        _FakeStreamEvent([_FakeStreamChoice(None, None)]),
        _FakeStreamEvent([_FakeStreamChoice("payload", None)]),
    ]
    fake_completions = _FakeCompletions(response, stream_events=stream_events)

    provider = GroqProvider(api_key="test-key")
    provider._client = _FakeClient(fake_completions)  # type: ignore[assignment]

    async def gather_chunks() -> list[ProviderChunk]:
        chunks: list[ProviderChunk] = []
        async for chunk in provider.stream_chat(
            messages=[ChatMessageSchema(role="user", content="hello")],
            model="openai/gpt-oss-20b",
        ):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(gather_chunks())

    assert chunks == [{"content": "payload", "finish_reason": None}]


def test_stream_chat_propagates_sdk_timeout_error() -> None:
    provider = GroqProvider(api_key="test-key")
    provider._client = _FakeClient(  # type: ignore[assignment]
        _RaisingCompletions(
            GroqAPITimeoutError(request=HTTPXRequest("POST", "https://example.test"))
        )
    )

    async def gather_chunks() -> list[ProviderChunk]:
        chunks: list[ProviderChunk] = []
        with pytest.raises(GroqAPITimeoutError):
            async for chunk in provider.stream_chat(
                messages=[ChatMessageSchema(role="user", content="hello")],
                model="openai/gpt-oss-20b",
            ):
                chunks.append(chunk)
        return chunks

    asyncio.run(gather_chunks())
