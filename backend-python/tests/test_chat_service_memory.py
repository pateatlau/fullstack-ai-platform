"""Phase 8 tests: Memory retrieve/inject/extract wiring in ``ChatService``.

Unit tests drive ``ChatService`` with in-memory fakes (``FakeChatStore``,
``FakeMemoryManager``) — no DB, no real Memory provider. Mirrors the
conventions in ``tests/test_chat_persistence.py``.
"""

from __future__ import annotations

import datetime
import uuid
from typing import AsyncIterator

import pytest
from pytest import MonkeyPatch
from starlette.requests import Request
from starlette.types import Message, Scope

from app.ai.memory.models import MemoryContext, MemoryRecord, MemoryScope, MemoryType
from app.ai.memory.summarizer import ConversationSummaryService
from app.ai.prompts.manager import create_prompt_manager
from app.core.caller import CallerContext
from app.core.config import Settings
from app.providers.base import ProviderChunk, ProviderCompletion
from app.providers.factory import ProviderFactory
from app.schemas.chat import ChatMessageSchema, ChatRequestSchema, ProviderName
from app.services.chat_service import (
    ChatService,
    ChatStore,
    EmptyProviderResponseError,
)
from app.services.quota_service import QuotaService
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


class MessageCapturingProvider(FakeProvider):
    """Records every ``messages`` list passed to ``complete_chat``."""

    def __init__(self, response: str = "Hello from the fake provider.") -> None:
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


class MessageCapturingStreamProvider(FakeProvider):
    """Records every ``messages`` list passed to ``stream_chat``."""

    def __init__(self, response: str = "streamed reply") -> None:
        super().__init__(response)
        self.received_messages: list[list[ChatMessageSchema]] = []

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


class _DisconnectedRequest:
    async def is_disconnected(self) -> bool:
        return True


def _patch_provider(monkeypatch: MonkeyPatch, provider: FakeProvider) -> None:
    def get_provider(
        name: ProviderName | None = None,
        settings: Settings | None = None,
    ) -> FakeProvider:
        _ = name, settings
        return provider

    monkeypatch.setattr(ProviderFactory, "get_provider", staticmethod(get_provider))


def _service(
    settings: Settings,
    *,
    chat_store: ChatStore | None = None,
    memory_manager: FakeMemoryManager | None = None,
    conversation_summary_service: ConversationSummaryService | None = None,
) -> ChatService:
    return ChatService(
        settings,
        chat_store=chat_store or FakeChatStore(),
        usage_store=FakeUsageStore(),
        quota_service=QuotaService(store=FakeGuestQuotaStore(), settings=settings),
        conversation_summary_service=conversation_summary_service,
        memory_manager=memory_manager,
    )


def _request(content: str, *, session_id: uuid.UUID | None = None) -> ChatRequestSchema:
    return ChatRequestSchema(
        messages=[ChatMessageSchema(role="user", content=content)],
        session_id=session_id,
    )


# --------------------------------------------------------------------------- #
# complete_chat: retrieve + inject                                             #
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_complete_chat_injects_memory_context_when_active(
    monkeypatch: MonkeyPatch,
) -> None:
    provider = MessageCapturingProvider("memory-aware reply")
    _patch_provider(monkeypatch, provider)
    settings = Settings(chat_persistence_enabled=True, memory_enabled=True)
    memory_manager = FakeMemoryManager(
        context=MemoryContext(user_memories=[_record("Loves TypeScript.")])
    )
    service = _service(settings, memory_manager=memory_manager)
    caller = CallerContext.for_user(uuid.uuid4())

    result = await service.complete_chat(_request("hi"), caller)

    assert result.content == "memory-aware reply"
    assert len(memory_manager.retrieve_calls) == 1
    assert memory_manager.retrieve_calls[0]["owner_id"] == caller.user_id
    llm_messages = provider.received_messages[-1]
    assert llm_messages[0].role == "system"
    assert "Loves TypeScript." in llm_messages[0].content


@pytest.mark.anyio
async def test_complete_chat_skips_memory_when_flag_disabled(
    monkeypatch: MonkeyPatch,
) -> None:
    provider = MessageCapturingProvider("plain reply")
    _patch_provider(monkeypatch, provider)
    settings = Settings(chat_persistence_enabled=True, memory_enabled=False)
    memory_manager = FakeMemoryManager(
        context=MemoryContext(user_memories=[_record("Loves TypeScript.")])
    )
    service = _service(settings, memory_manager=memory_manager)
    caller = CallerContext.for_user(uuid.uuid4())

    await service.complete_chat(_request("hi"), caller)

    assert memory_manager.retrieve_calls == []
    assert memory_manager.extraction_calls == []
    assert [m.role for m in provider.received_messages[-1]] == ["user"]


@pytest.mark.anyio
async def test_complete_chat_skips_memory_for_guest_caller(
    monkeypatch: MonkeyPatch,
) -> None:
    provider = MessageCapturingProvider()
    _patch_provider(monkeypatch, provider)
    settings = Settings(chat_persistence_enabled=True, memory_enabled=True)
    memory_manager = FakeMemoryManager(
        context=MemoryContext(user_memories=[_record("Loves TypeScript.")])
    )
    service = _service(settings, memory_manager=memory_manager)
    caller = CallerContext.anonymous(guest_id=uuid.uuid4())

    await service.complete_chat(_request("hi"), caller)

    assert memory_manager.retrieve_calls == []
    assert memory_manager.extraction_calls == []


@pytest.mark.anyio
async def test_memory_retrieval_failure_falls_back_to_original_messages(
    monkeypatch: MonkeyPatch,
) -> None:
    provider = MessageCapturingProvider("recovered reply")
    _patch_provider(monkeypatch, provider)
    settings = Settings(chat_persistence_enabled=True, memory_enabled=True)
    memory_manager = FakeMemoryManager(raise_on_retrieve=True)
    service = _service(settings, memory_manager=memory_manager)
    caller = CallerContext.for_user(uuid.uuid4())

    result = await service.complete_chat(_request("hi"), caller)

    assert result.content == "recovered reply"
    assert [m.role for m in provider.received_messages[-1]] == ["user"]
    # Extraction is independent of retrieval outcome — still scheduled.
    assert len(memory_manager.extraction_calls) == 1


# --------------------------------------------------------------------------- #
# complete_chat: async extraction                                              #
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_complete_chat_triggers_extraction_with_turn_content(
    monkeypatch: MonkeyPatch,
) -> None:
    _patch_provider(monkeypatch, FakeProvider("the answer"))
    settings = Settings(
        chat_persistence_enabled=True, memory_enabled=True, llm_provider="openai"
    )
    memory_manager = FakeMemoryManager()
    service = _service(settings, memory_manager=memory_manager)
    caller = CallerContext.for_user(uuid.uuid4())

    result = await service.complete_chat(_request("what is x?"), caller)

    assert len(memory_manager.extraction_calls) == 1
    call = memory_manager.extraction_calls[0]
    assert call["owner_id"] == caller.user_id
    assert call["session_id"] == result.session_id
    assert call["provider_name"] == "openai"
    turn = call["messages"]
    assert [(m.role, m.content) for m in turn] == [
        ("user", "what is x?"),
        ("assistant", "the answer"),
    ]


@pytest.mark.anyio
async def test_complete_chat_skips_extraction_on_empty_response(
    monkeypatch: MonkeyPatch,
) -> None:
    _patch_provider(monkeypatch, FakeProvider(""))
    settings = Settings(chat_persistence_enabled=True, memory_enabled=True)
    memory_manager = FakeMemoryManager()
    service = _service(settings, memory_manager=memory_manager)
    caller = CallerContext.for_user(uuid.uuid4())

    with pytest.raises(EmptyProviderResponseError):
        await service.complete_chat(_request("hi"), caller)

    assert memory_manager.extraction_calls == []


# --------------------------------------------------------------------------- #
# _resolve_llm_messages: bypass_summary_reconstruction (RAG-context fix)      #
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_bypass_summary_reconstruction_preserves_caller_messages() -> None:
    """Regression guard: without the bypass, DB-reconstructed summary context
    silently discards the caller-supplied ``request.messages`` (e.g. ephemeral
    RAG document context); the bypass must keep them intact while Memory is
    still applied on top.
    """
    settings = Settings(chat_persistence_enabled=True, memory_enabled=True)
    chat_store = FakeChatStore()
    user_id = uuid.uuid4()
    session = await chat_store.create_session(user_id=user_id)
    await chat_store.add_message(
        session_id=session.id, seq=1, role="user", content="earlier"
    )
    await chat_store.add_message(
        session_id=session.id, seq=2, role="assistant", content="earlier reply"
    )
    await chat_store.add_summary(
        session_id=session.id,
        version=1,
        covers_through_seq=2,
        content="Summary of earlier turns.",
        provider="openai",
        model="gpt-4o-mini",
    )
    memory_manager = FakeMemoryManager(
        context=MemoryContext(user_memories=[_record("Uses dark mode.")])
    )
    summary_service = ConversationSummaryService(
        chat_store=chat_store, prompt_manager=create_prompt_manager()
    )
    service = _service(
        settings,
        chat_store=chat_store,
        memory_manager=memory_manager,
        conversation_summary_service=summary_service,
    )
    caller = CallerContext.for_user(user_id)
    request = _request("What about now?", session_id=session.id)

    without_bypass = await service._resolve_llm_messages(session.id, request, caller)
    with_bypass = await service._resolve_llm_messages(
        session.id, request, caller, bypass_summary_reconstruction=True
    )

    # Without the bypass, DB reconstruction replaces request.messages outright.
    assert request.messages[0] not in without_bypass
    # With the bypass, the caller's own messages survive, memory prepended.
    assert with_bypass[0].role == "system"
    assert "Uses dark mode." in with_bypass[0].content
    assert with_bypass[1:] == request.messages


# --------------------------------------------------------------------------- #
# stream_chat: retrieve + inject + extraction                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_stream_chat_injects_memory_and_triggers_extraction(
    monkeypatch: MonkeyPatch,
) -> None:
    provider = MessageCapturingStreamProvider("streamed answer")
    _patch_provider(monkeypatch, provider)
    settings = Settings(
        chat_persistence_enabled=True, memory_enabled=True, llm_provider="openai"
    )
    memory_manager = FakeMemoryManager(
        context=MemoryContext(user_memories=[_record("Prefers Python.")])
    )
    service = _service(settings, memory_manager=memory_manager)
    caller = CallerContext.for_user(uuid.uuid4())
    request = _request("stream hi")

    prep = await service.prepare_stream(request, caller)
    assert prep is not None

    async for _ in service.stream_chat(
        request, _connected_request(), caller=caller, prep=prep
    ):
        pass

    assert len(memory_manager.retrieve_calls) == 1
    stream_messages = provider.received_messages[-1]
    assert stream_messages[0].role == "system"
    assert "Prefers Python." in stream_messages[0].content
    assert len(memory_manager.extraction_calls) == 1


@pytest.mark.anyio
async def test_stream_chat_skips_extraction_when_interrupted(
    monkeypatch: MonkeyPatch,
) -> None:
    _patch_provider(monkeypatch, FakeProvider("first second third"))
    settings = Settings(chat_persistence_enabled=True, memory_enabled=True)
    memory_manager = FakeMemoryManager()
    service = _service(settings, memory_manager=memory_manager)
    caller = CallerContext.for_user(uuid.uuid4())
    request = _request("stream hi")

    prep = await service.prepare_stream(request, caller)
    assert prep is not None

    async for _ in service.stream_chat(
        request,
        _DisconnectedRequest(),  # type: ignore[arg-type]
        caller=caller,
        prep=prep,
    ):
        pass

    assert memory_manager.extraction_calls == []
