"""Phase 8 tests: Memory wiring across ``UnifiedChatService`` execution paths.

All-fake, no-DB unit tests (mirrors ``test_unified_chat_persists_session_messages_with_documents``
in ``tests/test_unified_chat.py``) covering the branches that bypass
``ChatService``'s own message resolution: plain, document (RAG), tool-use
(web search), and streaming.
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import AsyncIterator
from typing import cast

import pytest
from pytest import MonkeyPatch
from starlette.requests import Request
from starlette.types import Message, Scope

from app.ai.memory.models import MemoryContext, MemoryRecord, MemoryScope, MemoryType
from app.ai.prompts.manager import create_prompt_manager
from app.ai.rag.context_builder import ContextBuilder
from app.ai.rag.retriever import Retriever
from app.ai.interfaces.vector_store import ScoredChunk
from app.ai.tools.executor import ToolExecutor
from app.ai.tools.implementations.web_search import (
    WEB_SEARCH_TOOL_NAME,
    WebSearchResult,
)
from app.ai.tools.registration import register_production_tools
from app.ai.tools.registry import ToolRegistry
from app.core.caller import CallerContext
from app.core.config import Settings
from app.providers.base import (
    ProviderChunk,
    ProviderCompletion,
    ProviderToolCall,
    ProviderToolCompletion,
)
from app.providers.factory import ProviderFactory
from app.schemas.chat import ChatMessageSchema, ChatRequestSchema, ProviderName
from app.services.chat_service import ChatService
from app.services.quota_service import QuotaService
from app.services.tool_chat_service import ToolChatService
from app.services.unified_chat_service import UnifiedChatService
from tests.fakes import (
    FakeChatStore,
    FakeGuestQuotaStore,
    FakeMemoryManager,
    FakeProvider,
    FakeUsageStore,
)

_NOW = datetime.datetime.now(datetime.timezone.utc)


def _record(content: str) -> MemoryRecord:
    return MemoryRecord(
        id=uuid.uuid4(),
        memory_type=MemoryType.USER,
        scope=MemoryScope.USER,
        owner_id=uuid.uuid4(),
        content=content,
        created_at=_NOW,
        updated_at=_NOW,
        source="api",
    )


class _CapturingProvider(FakeProvider):
    def __init__(self, response: str = "answer") -> None:
        super().__init__(response)
        self.received_messages: list[list[ChatMessageSchema]] = []

    async def complete_chat(
        self,
        messages: list[ChatMessageSchema],
        model: str,
        temperature: float = 0.7,
        *,
        max_tokens: int | None = None,
    ) -> ProviderCompletion:
        self.received_messages.append(list(messages))
        return await super().complete_chat(
            messages, model, temperature, max_tokens=max_tokens
        )

    async def stream_chat(
        self,
        messages: list[ChatMessageSchema],
        model: str,
        temperature: float = 0.7,
        *,
        max_tokens: int | None = None,
    ) -> AsyncIterator[ProviderChunk]:
        self.received_messages.append(list(messages))
        async for chunk in super().stream_chat(
            messages, model, temperature, max_tokens=max_tokens
        ):
            yield chunk


class _FakeRetriever:
    def __init__(self, content: str = "Plain text fixture content.") -> None:
        self._content = content

    async def retrieve(
        self, *, question: str, user_id: uuid.UUID, top_k: int | None
    ) -> list[ScoredChunk]:
        del question, top_k
        return [
            ScoredChunk(
                chunk_id=uuid.uuid4(),
                document_id=uuid.uuid4(),
                chunk_index=0,
                content=self._content,
                metadata={"source": "sample.txt"},
                score=0.95,
            )
        ]


class _FakeSearchClient:
    async def search(self, query: str, *, max_results: int) -> list[WebSearchResult]:
        del query, max_results
        return [
            WebSearchResult(
                title="Example", url="https://example.com", snippet="Example snippet"
            )
        ]


def _connected_request() -> Request:
    scope: Scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/chat/stream",
        "raw_path": b"/api/chat/stream",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive)


def _patch_provider(monkeypatch: MonkeyPatch, provider: FakeProvider) -> None:
    def get_provider(
        name: ProviderName | None = None,
        settings: Settings | None = None,
    ) -> FakeProvider:
        _ = name, settings
        return provider

    monkeypatch.setattr(ProviderFactory, "get_provider", staticmethod(get_provider))


def _unified_service(
    settings: Settings,
    *,
    memory_manager: FakeMemoryManager | None = None,
    retriever: object | None = None,
    tool_registry: ToolRegistry | None = None,
) -> tuple[UnifiedChatService, ChatService]:
    chat_store = FakeChatStore()
    chat_service = ChatService(
        settings,
        chat_store=chat_store,
        usage_store=FakeUsageStore(),
        quota_service=QuotaService(store=FakeGuestQuotaStore(), settings=settings),
        prompt_manager=create_prompt_manager(),
        memory_manager=memory_manager,
    )
    registry = tool_registry or ToolRegistry()
    tool_service = ToolChatService(
        chat_service=chat_service,
        tool_executor=ToolExecutor(registry=registry, settings=settings),
        tool_registry=registry,
        prompt_manager=create_prompt_manager(),
        settings=settings,
    )
    unified = UnifiedChatService(
        chat_service=chat_service,
        tool_chat_service=tool_service,
        retriever=cast(Retriever, retriever or _FakeRetriever()),
        context_builder=ContextBuilder(settings),
        prompt_manager=create_prompt_manager(),
        settings=settings,
    )
    return unified, chat_service


def _request(
    content: str,
    *,
    use_documents: bool = False,
    use_web_search: bool = False,
) -> ChatRequestSchema:
    return ChatRequestSchema(
        messages=[ChatMessageSchema(role="user", content=content)],
        provider="openai",
        model="gpt-4o-mini",
        use_documents=use_documents,
        use_web_search=use_web_search,
    )


# --------------------------------------------------------------------------- #
# execute(): plain chat                                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_execute_plain_chat_applies_memory_and_extracts(
    monkeypatch: MonkeyPatch,
) -> None:
    provider = _CapturingProvider("plain memory-aware answer")
    _patch_provider(monkeypatch, provider)
    settings = Settings(
        chat_persistence_enabled=True,
        memory_enabled=True,
        openai_api_key="test-key",
        rag_enabled=False,
        tools_enabled=False,
    )
    memory_manager = FakeMemoryManager(
        context=MemoryContext(user_memories=[_record("Likes bullet points.")])
    )
    unified, _ = _unified_service(settings, memory_manager=memory_manager)
    caller = CallerContext.for_user(uuid.uuid4())

    response = await unified.execute(_request("hi"), caller)

    assert response.content == "plain memory-aware answer"
    assert len(memory_manager.retrieve_calls) == 1
    assert provider.received_messages[-1][0].role == "system"
    assert "Likes bullet points." in provider.received_messages[-1][0].content
    assert len(memory_manager.extraction_calls) == 1


@pytest.mark.anyio
async def test_execute_skips_memory_when_flag_disabled(
    monkeypatch: MonkeyPatch,
) -> None:
    provider = _CapturingProvider("plain answer")
    _patch_provider(monkeypatch, provider)
    settings = Settings(
        chat_persistence_enabled=True,
        memory_enabled=False,
        openai_api_key="test-key",
        rag_enabled=False,
        tools_enabled=False,
    )
    memory_manager = FakeMemoryManager(
        context=MemoryContext(user_memories=[_record("Likes bullet points.")])
    )
    unified, _ = _unified_service(settings, memory_manager=memory_manager)
    caller = CallerContext.for_user(uuid.uuid4())

    await unified.execute(_request("hi"), caller)

    assert memory_manager.retrieve_calls == []
    assert memory_manager.extraction_calls == []
    assert [m.role for m in provider.received_messages[-1]] == ["user"]


# --------------------------------------------------------------------------- #
# execute(): documents (RAG) — memory must survive the doc-context merge      #
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_execute_with_documents_prepends_memory_before_document_context(
    monkeypatch: MonkeyPatch,
) -> None:
    provider = _CapturingProvider("Grounded answer with fixture content.")
    _patch_provider(monkeypatch, provider)
    settings = Settings(
        chat_persistence_enabled=True,
        memory_enabled=True,
        openai_api_key="test-key",
        rag_enabled=True,
        advanced_rag_enabled=False,
        tools_enabled=False,
    )
    memory_manager = FakeMemoryManager(
        context=MemoryContext(user_memories=[_record("Prefers concise answers.")])
    )
    unified, chat_service = _unified_service(settings, memory_manager=memory_manager)
    caller = CallerContext.for_user(uuid.uuid4())

    response = await unified.execute(
        _request("What is in my documents?", use_documents=True), caller
    )

    assert response.session_id is not None
    llm_messages = provider.received_messages[-1]
    assert llm_messages[0].role == "system"
    assert "Prefers concise answers." in llm_messages[0].content
    assert "Plain text fixture content." in llm_messages[-2].content
    assert llm_messages[-1].role == "user"
    assert len(memory_manager.retrieve_calls) == 1
    assert len(memory_manager.extraction_calls) == 1


# --------------------------------------------------------------------------- #
# execute(): web search (tool loop) — memory applied before the tool loop     #
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_execute_with_web_search_applies_memory_before_tool_loop(
    monkeypatch: MonkeyPatch,
) -> None:
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
    _patch_provider(monkeypatch, fake_provider)
    settings = Settings(
        chat_persistence_enabled=True,
        memory_enabled=True,
        openai_api_key="test-key",
        rag_enabled=False,
        tools_enabled=True,
        web_search_api_key="test-tavily-key",
    )
    registry = ToolRegistry()
    register_production_tools(registry, settings, web_search_client=_FakeSearchClient())
    memory_manager = FakeMemoryManager(
        context=MemoryContext(user_memories=[_record("Interested in AI news.")])
    )
    unified, _ = _unified_service(
        settings, memory_manager=memory_manager, tool_registry=registry
    )
    caller = CallerContext.for_user(uuid.uuid4())

    response = await unified.execute(
        _request("Search for news", use_web_search=True), caller
    )

    assert "Grounded answer" in response.content
    assert len(memory_manager.retrieve_calls) == 1
    # Index 1: [0]=tool-use system prompt, [1]=memory block prepended by
    # `_apply_memory_context` before the tool loop, [2]=the user turn.
    memory_message = cast(ChatMessageSchema, fake_provider.tool_call_messages[0][1])
    assert memory_message.role == "system"
    assert "Interested in AI news." in memory_message.content
    assert len(memory_manager.extraction_calls) == 1
    assert memory_manager.extraction_calls[0]["messages"][1].content == response.content


# --------------------------------------------------------------------------- #
# stream_execute(): memory applied for every streaming branch                 #
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_stream_execute_plain_chat_applies_memory(
    monkeypatch: MonkeyPatch,
) -> None:
    provider = _CapturingProvider("streamed memory-aware answer")
    _patch_provider(monkeypatch, provider)
    settings = Settings(
        chat_persistence_enabled=True,
        memory_enabled=True,
        openai_api_key="test-key",
        rag_enabled=False,
        tools_enabled=False,
    )
    memory_manager = FakeMemoryManager(
        context=MemoryContext(user_memories=[_record("Prefers metric units.")])
    )
    unified, chat_service = _unified_service(settings, memory_manager=memory_manager)
    caller = CallerContext.for_user(uuid.uuid4())
    request = _request("stream hi")

    prep = await chat_service.prepare_stream(request, caller)
    assert prep is not None

    async for _ in unified.stream_execute(
        request, _connected_request(), caller=caller, prep=prep
    ):
        pass

    assert len(memory_manager.retrieve_calls) == 1
    stream_messages = provider.received_messages[-1]
    assert stream_messages[0].role == "system"
    assert "Prefers metric units." in stream_messages[0].content
    assert len(memory_manager.extraction_calls) == 1
