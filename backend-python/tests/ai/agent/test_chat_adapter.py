"""Tests for chat agent adapters (Phase 11)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from pytest import MonkeyPatch

from app.ai.agent.adapters.chat_adapter import (
    CHAT_AGENT_MAX_ITERATIONS,
    ChatAgentAdapter,
    build_agent_context,
    build_agent_request,
)
from app.ai.agent.runtime import DefaultAgent, create_default_agent
from app.ai.agent.scratchpad import ScratchpadStore
from app.ai.deps import get_tool_registry
from app.ai.prompts.manager import create_prompt_manager
from app.ai.tools.executor import ToolExecutor
from app.ai.tools.implementations.web_search import WEB_SEARCH_TOOL_NAME
from app.ai.tools.registration import register_production_tools
from app.ai.tools.registry import ToolRegistry
from app.core.caller import CallerContext
from app.core.config import Settings, get_settings
from app.main import app
from app.providers.base import ProviderToolCall, ProviderToolCompletion
from app.providers.factory import ProviderFactory
from app.routers.chat import get_optional_caller
from app.schemas.chat import ChatMessageSchema, ChatRequestSchema
from app.services.chat_service import ChatService
from app.services.quota_service import QuotaService
from tests.fakes import FakeChatStore, FakeGuestQuotaStore, FakeProvider, FakeUsageStore


@pytest.fixture(autouse=True)
def _clear_settings_and_registry() -> Iterator[None]:
    get_settings.cache_clear()
    get_tool_registry.cache_clear()
    yield
    get_settings.cache_clear()
    get_tool_registry.cache_clear()
    app.dependency_overrides.clear()


@pytest.fixture
def scratchpad_store() -> Iterator[ScratchpadStore]:
    store = ScratchpadStore()
    yield store
    store.clear()


def _register_web_search_tools(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("TOOLS_ENABLED", "true")
    monkeypatch.setenv("WEB_SEARCH_API_KEY", "test-tavily-key")
    get_settings.cache_clear()
    registry = get_tool_registry()

    class _FakeSearchClient:
        async def search(self, query: str, *, max_results: int):
            del query, max_results
            return []

    register_production_tools(
        registry,
        Settings(tools_enabled=True, web_search_api_key="test-tavily-key"),
        web_search_client=_FakeSearchClient(),
    )


@pytest.fixture
def tool_registry(monkeypatch: MonkeyPatch) -> ToolRegistry:
    _register_web_search_tools(monkeypatch)
    return get_tool_registry()


def _mock_provider_factory(provider: FakeProvider):
    return staticmethod(lambda name=None, settings=None: provider)


def _chat_request(*, use_web_search: bool = True) -> ChatRequestSchema:
    return ChatRequestSchema(
        messages=[ChatMessageSchema(role="user", content="Search for news")],
        use_web_search=use_web_search,
        provider="openai",
        model="gpt-4o-mini",
    )


def _agent(
    *,
    provider: FakeProvider,
    tool_registry: ToolRegistry,
    scratchpad_store: ScratchpadStore,
    monkeypatch: pytest.MonkeyPatch,
) -> DefaultAgent:
    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        staticmethod(lambda _name, _settings: provider),
    )
    tool_executor = ToolExecutor(
        registry=tool_registry,
        settings=Settings(
            request_timeout_seconds=5,
            tools_enabled=True,
            web_search_api_key="test-tavily-key",
        ),
    )
    return create_default_agent(
        settings=Settings(
            request_timeout_seconds=5,
            tools_enabled=True,
            web_search_api_key="test-tavily-key",
        ),
        tool_registry=tool_registry,
        prompt_manager=create_prompt_manager(),
        tool_executor=tool_executor,
        scratchpad_store=scratchpad_store,
    )


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


def test_build_agent_request_maps_chat_fields() -> None:
    request = _chat_request()
    caller = CallerContext.for_user(uuid.uuid4())
    settings = Settings(openai_api_key="test-key", request_timeout_seconds=30)

    agent_request = build_agent_request(
        request=request,
        model="gpt-4o-mini",
        provider_name="openai",
        caller=caller,
        settings=settings,
        allowed_tool_names=frozenset({WEB_SEARCH_TOOL_NAME}),
    )

    assert len(agent_request.messages) == 1
    assert agent_request.messages[0].role == "user"
    assert agent_request.model == "gpt-4o-mini"
    assert agent_request.provider == "openai"
    assert agent_request.tool_names == [WEB_SEARCH_TOOL_NAME]
    assert agent_request.config is not None
    assert agent_request.config.max_iterations == CHAT_AGENT_MAX_ITERATIONS
    assert agent_request.config.timeout_seconds == 30


def test_build_agent_context_restricts_web_search_tools() -> None:
    caller = CallerContext.for_user(uuid.uuid4())
    context = build_agent_context(
        caller=caller,
        allowed_tool_names=frozenset({WEB_SEARCH_TOOL_NAME}),
    )
    assert context.caller == caller
    assert context.allowed_tool_names == frozenset({WEB_SEARCH_TOOL_NAME})


@pytest.mark.anyio
async def test_chat_agent_adapter_complete_chat_returns_tools_used(
    tool_registry: ToolRegistry,
    scratchpad_store: ScratchpadStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FakeProvider(
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
                content="Agent answer from web search.",
                tool_calls=[],
                finish_reason="stop",
            ),
        ]
    )
    agent = _agent(
        provider=provider,
        tool_registry=tool_registry,
        scratchpad_store=scratchpad_store,
        monkeypatch=monkeypatch,
    )
    chat_service = ChatService(
        Settings(openai_api_key="test-key", tools_enabled=True),
        prompt_manager=create_prompt_manager(),
    )
    adapter = ChatAgentAdapter(
        agent=agent,
        chat_service=chat_service,
        settings=Settings(openai_api_key="test-key", tools_enabled=True),
    )

    response = await adapter.complete_chat(
        _chat_request(),
        CallerContext.for_user(uuid.uuid4()),
        allowed_tool_names=frozenset({WEB_SEARCH_TOOL_NAME}),
    )

    assert "Agent answer" in response.content
    assert response.tools_used == [WEB_SEARCH_TOOL_NAME]
    assert provider.tool_completion_calls >= 1


@pytest.mark.anyio
async def test_chat_agent_adapter_persists_session_and_usage(
    tool_registry: ToolRegistry,
    scratchpad_store: ScratchpadStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = FakeProvider(
        response="Persisted agent answer.",
        tool_completions=[
            ProviderToolCompletion(
                content="Persisted agent answer.",
                tool_calls=[],
                finish_reason="stop",
            )
        ],
    )
    agent = _agent(
        provider=provider,
        tool_registry=tool_registry,
        scratchpad_store=scratchpad_store,
        monkeypatch=monkeypatch,
    )
    settings = Settings(
        chat_persistence_enabled=True,
        openai_api_key="test-key",
        tools_enabled=True,
        web_search_api_key="test-tavily-key",
    )
    chat_store = FakeChatStore()
    chat_service = ChatService(
        settings,
        chat_store=chat_store,
        usage_store=FakeUsageStore(),
        quota_service=QuotaService(store=FakeGuestQuotaStore(), settings=settings),
        prompt_manager=create_prompt_manager(),
    )
    adapter = ChatAgentAdapter(
        agent=agent, chat_service=chat_service, settings=settings
    )
    caller = CallerContext.for_user(uuid.uuid4())

    response = await adapter.complete_chat(
        _chat_request(),
        caller,
        allowed_tool_names=frozenset({WEB_SEARCH_TOOL_NAME}),
    )

    assert response.session_id is not None
    messages = await chat_store.list_messages(response.session_id)
    assert [message.role for message in messages] == ["user", "assistant"]
    assert messages[1].content == "Persisted agent answer."


@pytest.mark.anyio
async def test_unified_chat_agent_runtime_stream_sse_parity(
    monkeypatch: MonkeyPatch,
) -> None:
    _register_web_search_tools(monkeypatch)
    monkeypatch.setenv("AGENT_RUNTIME_ENABLED", "true")
    get_settings.cache_clear()

    provider = FakeProvider(
        tool_completions=[
            ProviderToolCompletion(
                content=None,
                tool_calls=[
                    ProviderToolCall(
                        id="call-stream",
                        name=WEB_SEARCH_TOOL_NAME,
                        arguments={"query": "news"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderToolCompletion(
                content="Grounded stream answer from web search",
                tool_calls=[],
                finish_reason="stop",
            ),
        ],
        response="Grounded stream answer from web search",
    )
    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        _mock_provider_factory(provider),
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
    start_index = events.index("start")
    first_delta_index = events.index("delta")
    assert start_index < first_delta_index
    delta_content = "".join(
        frame["content"] for event, frame in frames if event == "delta"
    )
    assert "Grounded stream answer from web search" == delta_content


@pytest.mark.anyio
async def test_unified_chat_agent_runtime_non_stream_parity(
    monkeypatch: MonkeyPatch,
) -> None:
    _register_web_search_tools(monkeypatch)
    monkeypatch.setenv("AGENT_RUNTIME_ENABLED", "true")
    get_settings.cache_clear()

    provider = FakeProvider(
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
                content="Grounded answer from agent runtime.",
                tool_calls=[],
                finish_reason="stop",
            ),
        ]
    )
    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        _mock_provider_factory(provider),
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
    body = response.json()
    assert "Grounded answer" in body["content"]
    assert body["tools_used"] == [WEB_SEARCH_TOOL_NAME]


@pytest.mark.anyio
async def test_unified_chat_flag_off_uses_legacy_tool_path(
    monkeypatch: MonkeyPatch,
) -> None:
    _register_web_search_tools(monkeypatch)
    provider = FakeProvider(
        tool_completions=[
            ProviderToolCompletion(
                content=None,
                tool_calls=[
                    ProviderToolCall(
                        id="call-legacy",
                        name=WEB_SEARCH_TOOL_NAME,
                        arguments={"query": "news"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderToolCompletion(
                content="Legacy tool path answer.",
                tool_calls=[],
                finish_reason="stop",
            ),
        ]
    )
    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        _mock_provider_factory(provider),
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
    assert "Legacy tool path answer." in response.json()["content"]
    assert provider.tool_completion_calls >= 1


@pytest.mark.anyio
async def test_stream_agent_chat_error_persists_when_sse_mapping_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock, MagicMock

    from app.ai.agent.adapters import chat_stream_adapter as stream_module
    from app.ai.agent.adapters.chat_stream_adapter import stream_agent_chat
    from app.ai.agent.models.events import AgentStreamEvent
    from app.schemas.chat import ChatMessageSchema, ChatRequestSchema

    error_event = AgentStreamEvent.error(
        "exec-error",
        code="agent_error",
        message="Something went wrong.",
    )

    async def _error_stream(*_args, **_kwargs):
        yield error_event

    agent = MagicMock()
    agent.stream = _error_stream

    persist = AsyncMock()
    chat_service = MagicMock()
    chat_service._persist_stream_result = persist

    monkeypatch.setattr(
        stream_module,
        "sse_frame_from_agent_event",
        lambda *_args, **_kwargs: None,
    )

    request = ChatRequestSchema(
        messages=[ChatMessageSchema(role="user", content="Search for news")],
        use_web_search=True,
        provider="openai",
        model="gpt-4o-mini",
    )
    http_request = MagicMock()
    http_request.is_disconnected = AsyncMock(return_value=False)
    provider = MagicMock()

    frames = [
        frame
        async for frame in stream_agent_chat(
            agent=agent,
            chat_service=chat_service,
            settings=Settings(openai_api_key="test-key", tools_enabled=True),
            request=request,
            http_request=http_request,
            caller=CallerContext.for_user(uuid.uuid4()),
            prep=None,
            response_id="resp_error",
            session_id=None,
            provider=provider,
            provider_name="openai",
            model="gpt-4o-mini",
            allowed_tool_names=frozenset({WEB_SEARCH_TOOL_NAME}),
        )
    ]

    assert any("event: start" in frame for frame in frames)
    assert any("event: error" in frame for frame in frames)
    assert "agent_error" in "".join(frames)
    assert "Something went wrong." in "".join(frames)
    persist.assert_awaited_once()
    assert persist.await_args is not None
    assert persist.await_args.kwargs["status"] == "error"
