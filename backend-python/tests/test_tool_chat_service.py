"""ToolChatService integration tests."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest
from pytest import MonkeyPatch

from app.ai.deps import get_prompt_manager, get_tool_registry
from app.ai.tools.executor import ToolExecutor
from app.ai.tools.implementations.web_search import (
    WEB_SEARCH_TOOL_DEFINITION,
    WebSearchResult,
    create_web_search_handler,
)
from app.ai.tools.registry import ToolRegistry
from app.core.caller import CallerContext
from app.core.config import Settings
from app.providers.base import ProviderToolCall, ProviderToolCompletion
from app.providers.capabilities import ProviderCapabilities, get_capabilities
from app.providers.factory import ProviderFactory
from app.schemas.chat import ChatMessageSchema, ChatRequestSchema
from app.services.chat_service import ChatService, ChatServiceError
from app.services.tool_chat_service import ToolChatService, _GUEST_TOOL_DENIED_MESSAGE
from tests.fakes import FakeProvider


class FakeWebSearchClient:
    def __init__(self, results: list[WebSearchResult] | None = None) -> None:
        self._results = results or [
            WebSearchResult(
                title="AI News",
                url="https://news.example/ai",
                snippet="Latest AI developments.",
            )
        ]

    async def search(self, query: str, *, max_results: int) -> list[WebSearchResult]:
        del query, max_results
        return self._results


@pytest.fixture(autouse=True)
def _clear_registry_cache() -> Iterator[None]:
    get_tool_registry.cache_clear()
    yield
    get_tool_registry.cache_clear()


@pytest.fixture
def tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        WEB_SEARCH_TOOL_DEFINITION,
        create_web_search_handler(
            settings=Settings(web_search_api_key="test-key"),
            client=FakeWebSearchClient(),
        ),
    )
    return registry


def _build_service(
    *,
    provider: FakeProvider,
    registry: ToolRegistry,
    settings: Settings | None = None,
    max_tool_iterations: int = 3,
) -> ToolChatService:
    settings = settings or Settings()
    chat_service = ChatService(settings, prompt_manager=get_prompt_manager())
    executor = ToolExecutor(registry=registry, settings=settings)
    return ToolChatService(
        chat_service=chat_service,
        tool_executor=executor,
        tool_registry=registry,
        prompt_manager=get_prompt_manager(),
        settings=settings,
        max_tool_iterations=max_tool_iterations,
    )


@pytest.mark.anyio
async def test_tool_loop_executes_search_and_returns_final_answer(
    tool_registry: ToolRegistry,
    monkeypatch: MonkeyPatch,
) -> None:
    provider = FakeProvider(
        tool_completions=[
            ProviderToolCompletion(
                content=None,
                tool_calls=[
                    ProviderToolCall(
                        id="call-1",
                        name="web_search",
                        arguments={"query": "latest AI news"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderToolCompletion(
                content="According to AI News — https://news.example/ai, there are new developments.",
                tool_calls=[],
                finish_reason="stop",
            ),
        ]
    )
    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        staticmethod(lambda name=None, settings=None: provider),
    )
    service = _build_service(provider=provider, registry=tool_registry)
    caller = CallerContext.for_user(uuid.uuid4())

    response = await service.complete_chat(
        ChatRequestSchema(
            messages=[
                ChatMessageSchema(role="user", content="What is the latest AI news?")
            ],
            provider="openai",
            model="gpt-4o-mini",
        ),
        caller,
    )

    assert "AI News" in response.content
    assert provider.tool_completion_calls == 2


@pytest.mark.anyio
async def test_tool_loop_emits_web_search_activity(
    tool_registry: ToolRegistry,
    monkeypatch: MonkeyPatch,
) -> None:
    provider = FakeProvider(
        tool_completions=[
            ProviderToolCompletion(
                content=None,
                tool_calls=[
                    ProviderToolCall(
                        id="call-1",
                        name="web_search",
                        arguments={"query": "weather"},
                    )
                ],
                finish_reason="tool_calls",
            ),
            ProviderToolCompletion(
                content="It is sunny.",
                tool_calls=[],
                finish_reason="stop",
            ),
        ]
    )
    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        staticmethod(lambda name=None, settings=None: provider),
    )
    service = _build_service(provider=provider, registry=tool_registry)
    caller = CallerContext.for_user(uuid.uuid4())
    phases: list[str] = []

    async def on_activity(phase: str) -> None:
        phases.append(phase)

    await service.complete_chat(
        ChatRequestSchema(
            messages=[ChatMessageSchema(role="user", content="Weather today?")],
            provider="openai",
            model="gpt-4o-mini",
        ),
        caller,
        on_activity=on_activity,
    )

    assert phases == ["web_search", "thinking"]


@pytest.mark.anyio
async def test_direct_answer_emits_no_web_search_activity(
    tool_registry: ToolRegistry,
    monkeypatch: MonkeyPatch,
) -> None:
    provider = FakeProvider(
        tool_completions=[
            ProviderToolCompletion(
                content="You are welcome!",
                tool_calls=[],
                finish_reason="stop",
            )
        ]
    )
    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        staticmethod(lambda name=None, settings=None: provider),
    )
    service = _build_service(provider=provider, registry=tool_registry)
    caller = CallerContext.for_user(uuid.uuid4())
    phases: list[str] = []

    async def on_activity(phase: str) -> None:
        phases.append(phase)

    await service.complete_chat(
        ChatRequestSchema(
            messages=[ChatMessageSchema(role="user", content="thanks")],
            provider="openai",
            model="gpt-4o-mini",
        ),
        caller,
        on_activity=on_activity,
    )

    assert phases == []


@pytest.mark.anyio
async def test_iteration_cap_stops_after_max_iterations(
    tool_registry: ToolRegistry,
    monkeypatch: MonkeyPatch,
) -> None:
    repeated = ProviderToolCompletion(
        content=None,
        tool_calls=[
            ProviderToolCall(
                id="call-repeat",
                name="web_search",
                arguments={"query": "loop"},
            )
        ],
        finish_reason="tool_calls",
    )
    provider = FakeProvider(tool_completions=[repeated, repeated, repeated, repeated])
    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        staticmethod(lambda name=None, settings=None: provider),
    )
    service = _build_service(
        provider=provider,
        registry=tool_registry,
        max_tool_iterations=3,
    )
    caller = CallerContext.for_user(uuid.uuid4())

    response = await service.complete_chat(
        ChatRequestSchema(
            messages=[ChatMessageSchema(role="user", content="Search forever")],
            provider="openai",
            model="gpt-4o-mini",
        ),
        caller,
    )

    assert provider.tool_completion_calls == 3
    assert "tool-use limit" in response.content.lower()


@pytest.mark.anyio
async def test_guest_tool_call_returns_graceful_message(
    tool_registry: ToolRegistry,
    monkeypatch: MonkeyPatch,
) -> None:
    provider = FakeProvider(
        tool_completions=[
            ProviderToolCompletion(
                content=None,
                tool_calls=[
                    ProviderToolCall(
                        id="call-guest",
                        name="web_search",
                        arguments={"query": "news"},
                    )
                ],
                finish_reason="tool_calls",
            )
        ]
    )
    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        staticmethod(lambda name=None, settings=None: provider),
    )
    service = _build_service(provider=provider, registry=tool_registry)
    caller = CallerContext.anonymous(guest_id=uuid.uuid4())

    response = await service.complete_chat(
        ChatRequestSchema(
            messages=[ChatMessageSchema(role="user", content="Search the web")],
            provider="openai",
            model="gpt-4o-mini",
        ),
        caller,
    )

    assert response.content == _GUEST_TOOL_DENIED_MESSAGE


@pytest.mark.anyio
async def test_unauthenticated_caller_denied_when_tools_registered(
    tool_registry: ToolRegistry,
    monkeypatch: MonkeyPatch,
) -> None:
    provider = FakeProvider(
        tool_completions=[
            ProviderToolCompletion(
                content=None,
                tool_calls=[
                    ProviderToolCall(
                        id="call-anon",
                        name="web_search",
                        arguments={"query": "news"},
                    )
                ],
                finish_reason="tool_calls",
            )
        ]
    )
    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        staticmethod(lambda name=None, settings=None: provider),
    )
    service = _build_service(provider=provider, registry=tool_registry)

    response = await service.complete_chat(
        ChatRequestSchema(
            messages=[ChatMessageSchema(role="user", content="Search the web")],
            provider="openai",
            model="gpt-4o-mini",
        ),
        None,
    )

    assert response.content == _GUEST_TOOL_DENIED_MESSAGE
    assert provider.tool_completion_calls == 1


@pytest.mark.anyio
async def test_unsupported_provider_returns_validation_error(
    tool_registry: ToolRegistry,
    monkeypatch: MonkeyPatch,
) -> None:
    provider = FakeProvider("Should not be reached")
    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        staticmethod(lambda name=None, settings=None: provider),
    )

    def _unsupported_capabilities(name: str) -> ProviderCapabilities:
        caps = get_capabilities(name)  # type: ignore[arg-type]
        if name == "openai":
            return ProviderCapabilities(
                supports_streaming=caps.supports_streaming,
                supports_tool_calling=False,
                supports_json_mode=caps.supports_json_mode,
                supports_reasoning=caps.supports_reasoning,
                supports_image_input=caps.supports_image_input,
                supports_image_output=caps.supports_image_output,
                supports_audio=caps.supports_audio,
                supports_embeddings=caps.supports_embeddings,
            )
        return caps

    monkeypatch.setattr(
        "app.services.tool_chat_service.get_capabilities",
        _unsupported_capabilities,
    )
    service = _build_service(provider=provider, registry=tool_registry)
    caller = CallerContext.for_user(uuid.uuid4())

    with pytest.raises(ChatServiceError) as exc_info:
        await service.complete_chat(
            ChatRequestSchema(
                messages=[ChatMessageSchema(role="user", content="Search the web")],
                provider="openai",
                model="gpt-4o-mini",
            ),
            caller,
        )

    assert exc_info.value.code == "validation_error"
    assert "not supported for provider 'openai'" in exc_info.value.message
    assert provider.tool_completion_calls == 0


@pytest.mark.anyio
async def test_no_tools_registered_falls_back_to_standard_completion(
    monkeypatch: MonkeyPatch,
) -> None:
    provider = FakeProvider("Plain answer without tools.")
    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        staticmethod(lambda name=None, settings=None: provider),
    )
    service = _build_service(provider=provider, registry=ToolRegistry())

    response = await service.complete_chat(
        ChatRequestSchema(
            messages=[ChatMessageSchema(role="user", content="Hello")],
            provider="openai",
            model="gpt-4o-mini",
        ),
        None,
    )

    assert response.content == "Plain answer without tools."
    assert provider.tool_completion_calls == 0
