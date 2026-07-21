"""Phase 4 chat endpoint integration tests (tools + streaming policy)."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from pytest import MonkeyPatch

from app.ai.deps import get_tool_registry
from app.ai.tools.implementations.web_search import (
    WEB_SEARCH_TOOL_NAME,
    WebSearchResult,
)
from app.ai.tools.registration import register_production_tools
from app.core.caller import CallerContext
from app.core.config import Settings, get_settings
from app.main import app
from app.providers.base import ProviderToolCall, ProviderToolCompletion
from app.providers.factory import ProviderFactory
from app.routers.chat import get_optional_caller
from tests.fakes import FakeProvider


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


@pytest.fixture(autouse=True)
def _clear_settings_and_registry() -> Iterator[None]:
    get_settings.cache_clear()
    get_tool_registry.cache_clear()
    yield
    get_settings.cache_clear()
    get_tool_registry.cache_clear()
    app.dependency_overrides.clear()


def _mock_provider_factory(provider: FakeProvider):
    return staticmethod(lambda name=None, settings=None: provider)


@pytest.mark.anyio
async def test_tools_disabled_uses_standard_chat_path(
    monkeypatch: MonkeyPatch,
) -> None:
    fake_provider = FakeProvider("Standard chat without tools")
    monkeypatch.setenv("TOOLS_ENABLED", "false")
    get_settings.cache_clear()
    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        _mock_provider_factory(fake_provider),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/chat",
            json={
                "messages": [{"role": "user", "content": "Hello"}],
                "provider": "openai",
                "model": "gpt-4o-mini",
            },
        )

    assert response.status_code == 200
    assert response.json()["content"] == "Standard chat without tools"
    assert fake_provider.tool_completion_calls == 0


@pytest.mark.anyio
@pytest.mark.parametrize("provider_name", ["openai", "gemini", "groq", "anthropic"])
async def test_tools_enabled_non_streaming_invokes_tool_loop_per_provider(
    monkeypatch: MonkeyPatch,
    provider_name: str,
) -> None:
    monkeypatch.setenv("TOOLS_ENABLED", "true")
    monkeypatch.setenv("WEB_SEARCH_API_KEY", "test-tavily-key")
    get_settings.cache_clear()

    registry = get_tool_registry()
    register_production_tools(
        registry,
        Settings(tools_enabled=True, web_search_api_key="test-tavily-key"),
        web_search_client=_FakeSearchClient(),
    )

    fake_provider = FakeProvider(
        tool_completions=[
            ProviderToolCompletion(
                content=None,
                tool_calls=[
                    ProviderToolCall(
                        id="call-1",
                        name=WEB_SEARCH_TOOL_NAME,
                        arguments={"query": "news"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderToolCompletion(
                content="Grounded answer from Example — https://example.com",
                tool_calls=[],
                finish_reason="stop",
            ),
        ]
    )
    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        _mock_provider_factory(fake_provider),
    )

    async def _authenticated_caller() -> CallerContext:
        return CallerContext.for_user(uuid.uuid4())

    app.dependency_overrides[get_optional_caller] = _authenticated_caller

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/chat",
            json={
                "messages": [{"role": "user", "content": "Search for news"}],
                "use_web_search": True,
                "provider": provider_name,
            },
        )

    assert response.status_code == 200
    assert "Grounded answer" in response.json()["content"]
    assert fake_provider.tool_completion_calls >= 1


@pytest.mark.anyio
async def test_tools_enabled_non_streaming_invokes_tool_loop(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("TOOLS_ENABLED", "true")
    monkeypatch.setenv("WEB_SEARCH_API_KEY", "test-tavily-key")
    get_settings.cache_clear()

    registry = get_tool_registry()
    register_production_tools(
        registry,
        Settings(tools_enabled=True, web_search_api_key="test-tavily-key"),
        web_search_client=_FakeSearchClient(),
    )

    fake_provider = FakeProvider(
        tool_completions=[
            ProviderToolCompletion(
                content=None,
                tool_calls=[
                    ProviderToolCall(
                        id="call-1",
                        name=WEB_SEARCH_TOOL_NAME,
                        arguments={"query": "news"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderToolCompletion(
                content="Grounded answer from Example — https://example.com",
                tool_calls=[],
                finish_reason="stop",
            ),
        ]
    )
    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        _mock_provider_factory(fake_provider),
    )

    async def _authenticated_caller() -> CallerContext:
        return CallerContext.for_user(uuid.uuid4())

    app.dependency_overrides[get_optional_caller] = _authenticated_caller

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/chat",
            json={
                "messages": [{"role": "user", "content": "Search for news"}],
                "use_web_search": True,
                "provider": "openai",
                "model": "gpt-4o-mini",
            },
        )

    assert response.status_code == 200
    assert "Grounded answer" in response.json()["content"]
    assert fake_provider.tool_completion_calls == 2


@pytest.mark.anyio
async def test_tools_enabled_ndjson_reports_web_search_activity(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("TOOLS_ENABLED", "true")
    monkeypatch.setenv("WEB_SEARCH_API_KEY", "test-tavily-key")
    get_settings.cache_clear()

    registry = get_tool_registry()
    register_production_tools(
        registry,
        Settings(tools_enabled=True, web_search_api_key="test-tavily-key"),
        web_search_client=_FakeSearchClient(),
    )

    fake_provider = FakeProvider(
        tool_completions=[
            ProviderToolCompletion(
                content=None,
                tool_calls=[
                    ProviderToolCall(
                        id="call-1",
                        name=WEB_SEARCH_TOOL_NAME,
                        arguments={"query": "news"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderToolCompletion(
                content="Grounded answer from Example — https://example.com",
                tool_calls=[],
                finish_reason="stop",
            ),
        ]
    )
    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        _mock_provider_factory(fake_provider),
    )

    async def _authenticated_caller() -> CallerContext:
        return CallerContext.for_user(uuid.uuid4())

    app.dependency_overrides[get_optional_caller] = _authenticated_caller

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/chat",
            headers={"Accept": "application/x-ndjson"},
            json={
                "messages": [{"role": "user", "content": "Search for news"}],
                "use_web_search": True,
                "provider": "openai",
                "model": "gpt-4o-mini",
            },
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    lines = [line for line in response.text.strip().split("\n") if line]
    assert any('"phase": "web_search"' in line for line in lines)
    assert any('"type": "complete"' in line for line in lines)
    assert fake_provider.tool_completion_calls == 2


def _tool_then_answer_completions() -> list[ProviderToolCompletion]:
    return [
        ProviderToolCompletion(
            content=None,
            tool_calls=[
                ProviderToolCall(
                    id="call-1",
                    name=WEB_SEARCH_TOOL_NAME,
                    arguments={"query": "news"},
                )
            ],
            finish_reason="tool_calls",
        ),
        ProviderToolCompletion(
            content=None,
            tool_calls=[],
            finish_reason="stop",
        ),
    ]


@pytest.mark.anyio
async def test_stream_with_use_web_search_emits_tool_events(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("TOOLS_ENABLED", "true")
    monkeypatch.setenv("WEB_SEARCH_API_KEY", "test-tavily-key")
    get_settings.cache_clear()

    registry = get_tool_registry()
    register_production_tools(
        registry,
        Settings(tools_enabled=True, web_search_api_key="test-tavily-key"),
        web_search_client=_FakeSearchClient(),
    )

    fake_provider = FakeProvider(
        response="Grounded stream answer from web search",
        tool_completions=_tool_then_answer_completions(),
    )
    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        _mock_provider_factory(fake_provider),
    )

    async def _authenticated_caller() -> CallerContext:
        return CallerContext.for_user(uuid.uuid4())

    app.dependency_overrides[get_optional_caller] = _authenticated_caller

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/chat/stream",
            json={
                "messages": [{"role": "user", "content": "Search for news"}],
                "use_web_search": True,
                "provider": "openai",
                "model": "gpt-4o-mini",
            },
        )

    frames = _parse_sse_frames(response.text)
    events = [event for event, _ in frames]

    assert response.status_code == 200
    assert "tool_start" in events
    assert "tool_end" in events
    assert events.index("tool_start") < events.index("start")
    assert events.index("tool_end") < events.index("start")
    assert "delta" in events
    assert events[-1] == "end"
    assert fake_provider.tool_completion_calls >= 1
    delta_content = "".join(
        frame["content"] for event, frame in frames if event == "delta"
    )
    assert "Grounded stream answer from web search" == delta_content


@pytest.mark.anyio
async def test_stream_without_tools_unchanged(
    monkeypatch: MonkeyPatch,
) -> None:
    fake_provider = FakeProvider("Stream without tool loop")
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
            json={
                "messages": [{"role": "user", "content": "Stream please"}],
                "provider": "openai",
                "model": "gpt-4o-mini",
            },
        )

    frames = _parse_sse_frames(response.text)

    assert response.status_code == 200
    assert fake_provider.tool_completion_calls == 0
    events = [event for event, _ in frames]
    assert events[0] == "start"
    assert events[-1] == "end"
    assert events.count("delta") == len(fake_provider.response.split(" "))
    assert "tool_start" not in response.text
    assert "Stream" in response.text


@pytest.mark.anyio
@pytest.mark.parametrize("provider_name", ["openai", "gemini", "groq", "anthropic"])
async def test_stream_use_web_search_per_provider(
    monkeypatch: MonkeyPatch,
    provider_name: str,
) -> None:
    monkeypatch.setenv("TOOLS_ENABLED", "true")
    monkeypatch.setenv("WEB_SEARCH_API_KEY", "test-tavily-key")
    get_settings.cache_clear()

    registry = get_tool_registry()
    register_production_tools(
        registry,
        Settings(tools_enabled=True, web_search_api_key="test-tavily-key"),
        web_search_client=_FakeSearchClient(),
    )

    fake_provider = FakeProvider(
        response=f"Stream answer for {provider_name}",
        tool_completions=_tool_then_answer_completions(),
    )
    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        _mock_provider_factory(fake_provider),
    )

    async def _authenticated_caller() -> CallerContext:
        return CallerContext.for_user(uuid.uuid4())

    app.dependency_overrides[get_optional_caller] = _authenticated_caller

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/chat/stream",
            json={
                "messages": [{"role": "user", "content": "Search for news"}],
                "use_web_search": True,
                "provider": provider_name,
            },
        )

    frames = _parse_sse_frames(response.text)

    assert response.status_code == 200
    assert "tool_start" in [event for event, _ in frames]
    assert "tool_end" in [event for event, _ in frames]
    assert provider_name in response.text


@pytest.mark.anyio
async def test_stream_tool_iteration_limit_mid_stream(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("TOOLS_ENABLED", "true")
    monkeypatch.setenv("WEB_SEARCH_API_KEY", "test-tavily-key")
    get_settings.cache_clear()

    registry = get_tool_registry()
    register_production_tools(
        registry,
        Settings(tools_enabled=True, web_search_api_key="test-tavily-key"),
        web_search_client=_FakeSearchClient(),
    )

    always_tool = ProviderToolCompletion(
        content=None,
        tool_calls=[
            ProviderToolCall(
                id="call-loop",
                name=WEB_SEARCH_TOOL_NAME,
                arguments={"query": "loop"},
            )
        ],
        finish_reason="tool_calls",
    )
    fake_provider = FakeProvider(
        response="Should not reach plain stream",
        tool_completions=[always_tool, always_tool, always_tool, always_tool],
    )
    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        _mock_provider_factory(fake_provider),
    )

    async def _authenticated_caller() -> CallerContext:
        return CallerContext.for_user(uuid.uuid4())

    app.dependency_overrides[get_optional_caller] = _authenticated_caller

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/chat/stream",
            json={
                "messages": [{"role": "user", "content": "Keep searching"}],
                "use_web_search": True,
                "provider": "openai",
                "model": "gpt-4o-mini",
            },
        )

    frames = _parse_sse_frames(response.text)

    assert response.status_code == 200
    assert sum(1 for event, _ in frames if event == "tool_start") == 3
    assert "tool-use limit" in response.text


@pytest.mark.anyio
async def test_stream_cancel_during_tool_execution(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("TOOLS_ENABLED", "true")
    monkeypatch.setenv("WEB_SEARCH_API_KEY", "test-tavily-key")
    get_settings.cache_clear()

    registry = get_tool_registry()
    register_production_tools(
        registry,
        Settings(tools_enabled=True, web_search_api_key="test-tavily-key"),
        web_search_client=_SlowSearchClient(),
    )

    fake_provider = FakeProvider(
        response="Late stream answer",
        tool_completions=_tool_then_answer_completions(),
    )
    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        _mock_provider_factory(fake_provider),
    )

    async def _authenticated_caller() -> CallerContext:
        return CallerContext.for_user(uuid.uuid4())

    app.dependency_overrides[get_optional_caller] = _authenticated_caller

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver", timeout=5.0
    ) as client:
        async with client.stream(
            "POST",
            "/api/chat/stream",
            json={
                "messages": [{"role": "user", "content": "Search slowly"}],
                "use_web_search": True,
                "provider": "openai",
                "model": "gpt-4o-mini",
            },
        ) as response:
            assert response.status_code == 200
            collected = ""
            async for chunk in response.aiter_text():
                collected += chunk
                if "tool_start" in collected:
                    await response.aclose()
                    break

    assert "tool_start" in collected


@pytest.mark.anyio
async def test_stream_with_use_documents_returns_422(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAG_ENABLED", "true")
    get_settings.cache_clear()

    async def _authenticated_caller() -> CallerContext:
        return CallerContext.for_user(uuid.uuid4())

    app.dependency_overrides[get_optional_caller] = _authenticated_caller

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.post(
            "/api/chat/stream",
            json={
                "messages": [{"role": "user", "content": "Ask my docs"}],
                "use_documents": True,
                "provider": "openai",
                "model": "gpt-4o-mini",
            },
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_startup_registers_web_search_when_tools_enabled(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("TOOLS_ENABLED", "true")
    monkeypatch.setenv("WEB_SEARCH_API_KEY", "test-tavily-key")
    get_settings.cache_clear()
    get_tool_registry.cache_clear()

    registry = get_tool_registry()
    register_production_tools(
        registry,
        Settings(tools_enabled=True, web_search_api_key="test-tavily-key"),
        web_search_client=_FakeSearchClient(),
    )

    assert registry.get(WEB_SEARCH_TOOL_NAME) is not None


def test_startup_does_not_register_web_search_when_tools_disabled() -> None:
    get_tool_registry.cache_clear()
    registry = get_tool_registry()
    assert registry.get(WEB_SEARCH_TOOL_NAME) is None


class _FakeSearchClient:
    async def search(self, query: str, *, max_results: int) -> list[WebSearchResult]:
        del query, max_results
        return [
            WebSearchResult(
                title="Example",
                url="https://example.com",
                snippet="Example snippet",
            )
        ]


class _SlowSearchClient:
    async def search(self, query: str, *, max_results: int) -> list[WebSearchResult]:
        del query, max_results
        await asyncio.sleep(2.0)
        return [
            WebSearchResult(
                title="Slow Example",
                url="https://example.com/slow",
                snippet="Slow snippet",
            )
        ]
