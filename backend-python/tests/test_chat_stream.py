import asyncio
import json
from typing import Any, AsyncIterator, cast

import pytest
from httpx import ASGITransport, AsyncClient
from pytest import MonkeyPatch

from app.main import app
from app.core.config import Settings
from app.providers.base import ProviderChunk
from app.providers.factory import ProviderFactory
from app.schemas.chat import ChatMessageSchema, ChatRequestSchema
from app.services.chat_service import ChatService
from tests.fakes import FakeProvider
from tests.provider_error_assertions import assert_no_provider_sdk_leakage


def _parse_sse_frames(payload: str) -> list[tuple[str, dict[str, Any]]]:
    frames: list[tuple[str, dict[str, Any]]] = []
    for block in payload.strip().split("\n\n"):
        if not block:
            continue
        event = next(
            line.removeprefix("event: ")
            for line in block.splitlines()
            if line.startswith("event: ")
        )
        data = next(
            line.removeprefix("data: ")
            for line in block.splitlines()
            if line.startswith("data: ")
        )
        frames.append((event, json.loads(data)))
    return frames


class ErroringStreamProvider(FakeProvider):
    async def stream_chat(
        self,
        messages: list[ChatMessageSchema],
        model: str,
        temperature: float = 0.7,
        *,
        max_tokens: int | None = None,
    ) -> AsyncIterator[ProviderChunk]:
        del messages, model, temperature, max_tokens
        raise RuntimeError("provider exploded")
        yield  # pragma: no cover


class RecordingProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__("first second")
        self.chunks_seen = 0
        self.closed = False

    async def stream_chat(
        self,
        messages: list[ChatMessageSchema],
        model: str,
        temperature: float = 0.7,
        *,
        max_tokens: int | None = None,
    ) -> AsyncIterator[ProviderChunk]:
        del messages, model, temperature, max_tokens
        chunks: tuple[ProviderChunk, ProviderChunk] = (
            ProviderChunk(content="first ", finish_reason=None),
            ProviderChunk(content="second", finish_reason="stop"),
        )
        try:
            for chunk in chunks:
                self.chunks_seen += 1
                yield chunk
        finally:
            self.closed = True


class CapturingStreamProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__("default stream response")
        self.last_model: str | None = None

    async def stream_chat(
        self,
        messages: list[ChatMessageSchema],
        model: str,
        temperature: float = 0.7,
        *,
        max_tokens: int | None = None,
    ) -> AsyncIterator[ProviderChunk]:
        del messages, temperature, max_tokens
        self.last_model = model
        yield ProviderChunk(content="default stream response", finish_reason="stop")


class EmptyStreamProvider(FakeProvider):
    async def stream_chat(
        self,
        messages: list[ChatMessageSchema],
        model: str,
        temperature: float = 0.7,
        *,
        max_tokens: int | None = None,
    ) -> AsyncIterator[ProviderChunk]:
        del messages, model, temperature, max_tokens
        if False:  # pragma: no cover - keeps this an async generator
            yield ProviderChunk(content="", finish_reason=None)


class DisconnectAfterFirstChunkRequest:
    def __init__(self) -> None:
        self.calls = 0

    async def is_disconnected(self) -> bool:
        self.calls += 1
        return self.calls > 1


def _mock_provider_factory(provider: FakeProvider):
    def get_provider(
        name: str | None = None, settings: Settings | None = None
    ) -> FakeProvider:
        del name, settings
        return provider

    return staticmethod(get_provider)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider", "expected_model"),
    [
        ("openai", "gpt-4o-mini"),
        ("gemini", "gemini-3.1-flash-lite"),
        ("groq", "openai/gpt-oss-20b"),
        ("anthropic", "claude-haiku-4-5-20251001"),
    ],
)
async def test_chat_stream_uses_provider_default_model_for_supported_providers(
    monkeypatch: MonkeyPatch,
    provider: str,
    expected_model: str,
) -> None:
    stream_provider = CapturingStreamProvider()

    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        _mock_provider_factory(stream_provider),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/chat/stream",
            json={
                "messages": [{"role": "user", "content": "Hello"}],
                "provider": provider,
            },
        )

    frames = _parse_sse_frames(response.text)

    assert response.status_code == 200
    assert [event for event, _ in frames] == ["start", "delta", "end"]
    assert frames[1][1]["content"] == "default stream response"
    assert frames[-1][1]["finish_reason"] == "stop"
    assert stream_provider.last_model == expected_model


@pytest.mark.anyio
async def test_chat_stream_yields_start_delta_and_end(
    monkeypatch: MonkeyPatch,
) -> None:
    fake_provider = FakeProvider("Hello from stream")

    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        _mock_provider_factory(fake_provider),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/chat/stream",
            json={"messages": [{"role": "user", "content": "Hello"}]},
        )

    frames = _parse_sse_frames(response.text)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert [event for event, _ in frames] == ["start", "delta", "delta", "delta", "end"]
    assert (
        "".join(frame["content"] for event, frame in frames if event == "delta")
        == "Hello from stream"
    )
    assert frames[-1][1]["finish_reason"] == "stop"


@pytest.mark.anyio
async def test_chat_stream_surfaces_provider_error_frame(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        _mock_provider_factory(ErroringStreamProvider()),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/chat/stream",
            json={"messages": [{"role": "user", "content": "Hello"}]},
        )

    frames = _parse_sse_frames(response.text)

    assert response.status_code == 200
    assert [event for event, _ in frames] == ["start", "error"]
    assert frames[-1][1]["code"] == "provider_error"
    assert frames[-1][1]["message"] == "Upstream provider failed."
    assert_no_provider_sdk_leakage(frames[-1][1]["message"])


@pytest.mark.anyio
async def test_chat_stream_surfaces_empty_provider_response_as_error_frame(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        _mock_provider_factory(EmptyStreamProvider()),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/chat/stream",
            json={"messages": [{"role": "user", "content": "Hello"}]},
        )

    frames = _parse_sse_frames(response.text)

    assert response.status_code == 200
    assert [event for event, _ in frames] == ["start", "error"]
    assert frames[-1][1]["code"] == "empty_provider_response"
    assert "empty response" in frames[-1][1]["message"].lower()


def test_chat_service_stops_streaming_when_client_disconnects(
    monkeypatch: MonkeyPatch,
) -> None:
    provider = RecordingProvider()
    request = DisconnectAfterFirstChunkRequest()
    service = ChatService()

    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        _mock_provider_factory(provider),
    )

    async def collect_events() -> list[str]:
        request_model = ChatRequestSchema(
            messages=[ChatMessageSchema(role="user", content="Hello")]
        )
        return [
            chunk
            async for chunk in service.stream_chat(
                request_model,
                cast(Any, request),
            )
        ]

    chunks = asyncio.run(collect_events())
    frames = _parse_sse_frames("".join(chunks))

    assert [event for event, _ in frames] == ["start", "delta"]
    assert provider.chunks_seen == 1
    assert provider.closed is True


@pytest.mark.anyio
async def test_chat_stream_disabled_returns_503(monkeypatch: MonkeyPatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("CHAT_STREAMING_ENABLED", "false")
    get_settings.cache_clear()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/chat/stream",
            json={"messages": [{"role": "user", "content": "Hello"}]},
        )

    get_settings.cache_clear()
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "feature_disabled"
