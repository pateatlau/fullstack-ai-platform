"""Tests for ConversationSummaryService (Epic 05 Phase 2)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from app.ai.deps import get_conversation_summary_service, get_prompt_manager
from app.ai.memory.summarizer import ConversationSummaryService
from app.core.caller import CallerContext
from app.core.config import Settings
from app.providers.base import ProviderCompletion
from app.providers.factory import ProviderFactory
from app.schemas.chat import ChatMessageSchema, ChatRequestSchema
from app.services.chat_service import ChatService
from app.services.quota_service import QuotaService
from tests.fakes import FakeChatStore, FakeGuestQuotaStore, FakeProvider, FakeUsageStore


def _summary_service(chat_store: FakeChatStore) -> ConversationSummaryService:
    return ConversationSummaryService(
        chat_store=chat_store,
        prompt_manager=get_prompt_manager(),
    )


@pytest.mark.anyio
async def test_retrieve_summary_populates_memory_context() -> None:
    chat_store = FakeChatStore()
    session = await chat_store.create_session(user_id=uuid.uuid4())
    await chat_store.add_summary(
        session_id=session.id,
        version=1,
        covers_through_seq=4,
        content="User prefers TypeScript.",
        provider="openai",
        model="gpt-4o-mini",
    )
    service = _summary_service(chat_store)

    context = await service.retrieve_summary(session.id)

    assert context.conversation_summary == "User prefers TypeScript."
    assert context.metadata["summary_version"] == 1
    assert context.metadata["covers_through_seq"] == 4


@pytest.mark.anyio
async def test_retrieve_summary_empty_when_no_summary() -> None:
    chat_store = FakeChatStore()
    session = await chat_store.create_session(user_id=uuid.uuid4())
    service = _summary_service(chat_store)

    context = await service.retrieve_summary(session.id)

    assert context.conversation_summary is None
    assert context.metadata == {}


@pytest.mark.anyio
async def test_retrieve_summary_handles_store_failure() -> None:
    chat_store = AsyncMock()
    chat_store.get_latest_summary.side_effect = RuntimeError("db down")
    service = _summary_service(chat_store)  # type: ignore[arg-type]

    context = await service.retrieve_summary(uuid.uuid4())

    assert context.conversation_summary is None


@pytest.mark.anyio
async def test_build_context_messages_matches_summary_plus_tail() -> None:
    chat_store = FakeChatStore()
    session = await chat_store.create_session(user_id=uuid.uuid4())
    seed = [("user", "q1"), ("assistant", "a1"), ("user", "q2"), ("assistant", "a2")]
    for seq, (role, content) in enumerate(seed, start=1):
        await chat_store.add_message(
            session_id=session.id, seq=seq, role=role, content=content
        )
    await chat_store.add_summary(
        session_id=session.id,
        version=1,
        covers_through_seq=2,
        content="Earlier: greeting.",
        provider="openai",
        model="gpt-4o-mini",
    )
    service = _summary_service(chat_store)

    context = await service.build_context_messages(session.id)

    assert context[0].role == "system"
    assert "Earlier: greeting." in context[0].content
    assert [(m.role, m.content) for m in context[1:]] == [
        ("user", "q2"),
        ("assistant", "a2"),
    ]


@pytest.mark.anyio
async def test_build_context_messages_returns_empty_on_failure() -> None:
    chat_store = AsyncMock()
    chat_store.get_latest_summary.side_effect = RuntimeError("db down")
    service = _summary_service(chat_store)  # type: ignore[arg-type]

    context = await service.build_context_messages(uuid.uuid4())

    assert context == []


@pytest.mark.anyio
async def test_resolve_persisted_context_fetches_summary_once() -> None:
    chat_store = FakeChatStore()
    session = await chat_store.create_session(user_id=uuid.uuid4())
    for seq, (role, content) in enumerate(
        [("user", "q1"), ("assistant", "a1"), ("user", "q2")], start=1
    ):
        await chat_store.add_message(
            session_id=session.id, seq=seq, role=role, content=content
        )
    await chat_store.add_summary(
        session_id=session.id,
        version=1,
        covers_through_seq=2,
        content="Earlier: greeting.",
        provider="openai",
        model="gpt-4o-mini",
    )
    original_get_latest = chat_store.get_latest_summary
    call_count = 0

    async def counting_get_latest(session_id: uuid.UUID):
        nonlocal call_count
        call_count += 1
        return await original_get_latest(session_id)

    chat_store.get_latest_summary = counting_get_latest  # type: ignore[method-assign]
    service = _summary_service(chat_store)

    summary_context, context_messages = await service.resolve_persisted_context(
        session.id
    )

    assert call_count == 1
    assert summary_context.conversation_summary == "Earlier: greeting."
    assert context_messages[0].role == "system"
    assert "Earlier: greeting." in context_messages[0].content
    assert context_messages[-1].content == "q2"


@pytest.mark.anyio
async def test_trigger_summarization_delegates_to_chat_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chat_store = FakeChatStore()
    service = _summary_service(chat_store)
    delegate = AsyncMock()
    caller = CallerContext.for_user(uuid.uuid4())
    session_id = uuid.uuid4()
    provider = FakeProvider()

    await service.trigger_summarization(
        delegate=delegate,
        caller=caller,
        session_id=session_id,
        provider=provider,
        provider_name="openai",
        model="gpt-4o-mini",
    )

    delegate._maybe_summarize.assert_awaited_once_with(
        caller=caller,
        session_id=session_id,
        provider=provider,
        provider_name="openai",
        model="gpt-4o-mini",
    )


@pytest.mark.anyio
async def test_trigger_summarization_swallows_delegate_failure() -> None:
    chat_store = FakeChatStore()
    service = _summary_service(chat_store)
    delegate = AsyncMock()
    delegate._maybe_summarize.side_effect = RuntimeError("llm failed")

    await service.trigger_summarization(
        delegate=delegate,
        caller=CallerContext.for_user(uuid.uuid4()),
        session_id=uuid.uuid4(),
        provider=FakeProvider(),
        provider_name="openai",
        model="gpt-4o-mini",
    )


def test_get_conversation_summary_service_factory() -> None:
    session = AsyncMock()
    provider = get_conversation_summary_service(
        session=session,
        prompt_manager=get_prompt_manager(),
    )

    assert isinstance(provider, ConversationSummaryService)


class RecordingProvider(FakeProvider):
    def __init__(self, response: str = "reply") -> None:
        super().__init__(response=response)
        self.complete_messages: list[list[ChatMessageSchema]] = []

    async def complete_chat(
        self,
        messages: list[ChatMessageSchema],
        model: str,
        temperature: float = 0.7,
        *,
        max_tokens: int | None = None,
    ) -> ProviderCompletion:
        self.complete_messages.append(list(messages))
        return await super().complete_chat(
            messages, model, temperature, max_tokens=max_tokens
        )


def _memory_chat_service(
    *,
    chat_store: FakeChatStore,
    provider: RecordingProvider,
    memory_enabled: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> ChatService:
    def get_provider(
        name: str | None = None, settings: Settings | None = None
    ) -> RecordingProvider:
        return provider

    monkeypatch.setattr(ProviderFactory, "get_provider", staticmethod(get_provider))
    settings = Settings(
        chat_persistence_enabled=True,
        memory_enabled=memory_enabled,
        llm_provider="openai",
        openai_api_key="test-key",
        summary_trigger_message_count=50,
    )
    summary_service = ConversationSummaryService(
        chat_store=chat_store,
        prompt_manager=get_prompt_manager(),
    )
    return ChatService(
        settings,
        chat_store=chat_store,
        usage_store=FakeUsageStore(),
        quota_service=QuotaService(store=FakeGuestQuotaStore(), settings=settings),
        conversation_summary_service=summary_service,
    )


@pytest.mark.anyio
async def test_memory_enabled_uses_bounded_context_not_client_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = RecordingProvider("assistant reply")
    chat_store = FakeChatStore()
    service = _memory_chat_service(
        chat_store=chat_store,
        provider=provider,
        memory_enabled=True,
        monkeypatch=monkeypatch,
    )
    caller = CallerContext.for_user(uuid.uuid4())
    session = await chat_store.create_session(user_id=caller.user_id)
    for seq in range(1, 11):
        role = "user" if seq % 2 == 1 else "assistant"
        await chat_store.add_message(
            session_id=session.id,
            seq=seq,
            role=role,
            content=f"message-{seq}",
        )
    session.next_seq = 11
    await chat_store.add_summary(
        session_id=session.id,
        version=1,
        covers_through_seq=8,
        content="Summarized earlier turns.",
        provider="openai",
        model="gpt-4o-mini",
    )

    client_messages = [
        ChatMessageSchema(role="user", content=f"client-{i}") for i in range(20)
    ]
    client_messages.append(
        ChatMessageSchema(role="user", content="latest user question")
    )

    await service.complete_chat(
        ChatRequestSchema(messages=client_messages, session_id=session.id),
        caller,
    )

    assert len(provider.complete_messages) == 1
    sent = provider.complete_messages[0]
    assert sent[0].role == "system"
    assert "Summarized earlier turns." in sent[0].content
    assert [(m.role, m.content) for m in sent[1:]] == [
        ("user", "message-9"),
        ("assistant", "message-10"),
        ("user", "latest user question"),
    ]


@pytest.mark.anyio
async def test_memory_disabled_uses_client_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = RecordingProvider("assistant reply")
    chat_store = FakeChatStore()
    service = _memory_chat_service(
        chat_store=chat_store,
        provider=provider,
        memory_enabled=False,
        monkeypatch=monkeypatch,
    )
    caller = CallerContext.for_user(uuid.uuid4())
    client_messages = [ChatMessageSchema(role="user", content="only client history")]

    await service.complete_chat(
        ChatRequestSchema(messages=client_messages),
        caller,
    )

    assert provider.complete_messages[0] == client_messages


@pytest.mark.anyio
async def test_memory_enabled_guest_keeps_client_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = RecordingProvider("assistant reply")
    chat_store = FakeChatStore()
    service = _memory_chat_service(
        chat_store=chat_store,
        provider=provider,
        memory_enabled=True,
        monkeypatch=monkeypatch,
    )
    guest_id = uuid.uuid4()
    caller = CallerContext(kind="guest", guest_id=guest_id, user_id=None)
    client_messages = [ChatMessageSchema(role="user", content="guest question")]

    await service.complete_chat(
        ChatRequestSchema(messages=client_messages),
        caller,
    )

    assert provider.complete_messages[0] == client_messages


@pytest.mark.anyio
async def test_memory_enabled_summarization_still_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = RecordingProvider("A concise summary.")
    chat_store = FakeChatStore()
    service = _memory_chat_service(
        chat_store=chat_store,
        provider=provider,
        memory_enabled=True,
        monkeypatch=monkeypatch,
    )
    service._settings = service._settings.model_copy(
        update={"summary_trigger_message_count": 2}
    )
    caller = CallerContext.for_user(uuid.uuid4())

    result = await service.complete_chat(
        ChatRequestSchema(messages=[ChatMessageSchema(role="user", content="hi")]),
        caller,
    )

    assert result.session_id is not None
    summary = await chat_store.get_latest_summary(result.session_id)
    assert summary is not None
    assert summary.content == "A concise summary."


@pytest.mark.anyio
async def test_fallback_summarization_failure_does_not_fail_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = RecordingProvider("assistant reply")
    chat_store = FakeChatStore()
    service = _memory_chat_service(
        chat_store=chat_store,
        provider=provider,
        memory_enabled=False,
        monkeypatch=monkeypatch,
    )

    async def failing_summarize(**kwargs: object) -> None:
        raise RuntimeError("store error during summarize")

    monkeypatch.setattr(service, "_maybe_summarize", failing_summarize)

    result = await service.complete_chat(
        ChatRequestSchema(messages=[ChatMessageSchema(role="user", content="hi")]),
        CallerContext.for_user(uuid.uuid4()),
    )

    assert result.content == "assistant reply"
