"""Phase 4 chat endpoint integration tests (tools + streaming policy)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

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
                "provider": "openai",
                "model": "gpt-4o-mini",
            },
        )

    assert response.status_code == 200
    assert "Grounded answer" in response.json()["content"]
    assert fake_provider.tool_completion_calls == 2


@pytest.mark.anyio
async def test_streaming_skips_tools_even_when_enabled(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("TOOLS_ENABLED", "true")
    monkeypatch.setenv("WEB_SEARCH_API_KEY", "test-tavily-key")
    get_settings.cache_clear()

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

    assert response.status_code == 200
    assert fake_provider.tool_completion_calls == 0
    assert "event: delta" in response.text
    assert "Stream" in response.text


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
