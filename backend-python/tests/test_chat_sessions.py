"""Phase 1/2 tests: guest single-default-chat, provider gating, session list/create.

Unit tests drive ``ChatService`` with in-memory fakes (no DB), matching the
conventions in ``test_chat_persistence.py``. Integration tests exercise the
real ``SqlChatStore`` against the compose Postgres and skip automatically when
the database is unavailable (the ``db_session`` fixture from conftest provides
the skip guard).
"""

import uuid

import pytest
from pytest import MonkeyPatch

from app.core.caller import CallerContext
from app.core.config import Settings
from app.db.chat import SqlChatStore
from app.db.identity import SqlGuestStore, SqlUserStore
from app.providers.factory import ProviderFactory
from app.schemas.chat import ChatMessageSchema, ChatRequestSchema, ProviderName
from app.services.chat_service import (
    ChatService,
    NewChatForbiddenError,
    ProviderNotAllowedError,
    SessionNotFoundError,
)
from app.services.quota_service import QuotaService
from tests.fakes import FakeChatStore, FakeGuestQuotaStore, FakeProvider, FakeUsageStore


class RecordingProvider(FakeProvider):
    """Fake provider that counts how many times completion/streaming is requested."""

    def __init__(self, response: str = "recorded") -> None:
        super().__init__(response)
        self.complete_calls = 0

    async def complete_chat(
        self,
        messages,
        model,
        temperature: float = 0.7,
        *,
        max_tokens: int | None = None,
    ):  # type: ignore[override]
        self.complete_calls += 1
        return await super().complete_chat(
            messages, model, temperature, max_tokens=max_tokens
        )


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
    chat_store: FakeChatStore | None = None,
    usage_store: FakeUsageStore | None = None,
    quota_store: FakeGuestQuotaStore | None = None,
) -> ChatService:
    return ChatService(
        settings,
        chat_store=chat_store or FakeChatStore(),
        usage_store=usage_store or FakeUsageStore(),
        quota_service=QuotaService(
            store=quota_store or FakeGuestQuotaStore(), settings=settings
        ),
    )


def _request(
    content: str,
    *,
    model: str | None = None,
    provider: ProviderName | None = None,
    session_id: uuid.UUID | None = None,
) -> ChatRequestSchema:
    return ChatRequestSchema(
        messages=[ChatMessageSchema(role="user", content=content)],
        model=model,
        provider=provider,
        session_id=session_id,
    )


# --------------------------------------------------------------------------- #
# Guest single-default-chat enforcement                                       #
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_guest_without_session_id_reuses_existing_default_session(
    monkeypatch: MonkeyPatch,
) -> None:
    _patch_provider(monkeypatch, FakeProvider("reply"))
    settings = Settings(chat_persistence_enabled=True, llm_provider="openai")
    chat_store = FakeChatStore()
    service = _service(settings, chat_store=chat_store)
    caller = CallerContext.anonymous(guest_id=uuid.uuid4())

    first = await service.complete_chat(_request("first turn"), caller)
    second = await service.complete_chat(_request("second turn"), caller)

    assert first.session_id is not None
    assert second.session_id == first.session_id
    assert len(chat_store.sessions) == 1


@pytest.mark.anyio
async def test_guest_addressing_non_default_session_id_returns_404(
    monkeypatch: MonkeyPatch,
) -> None:
    _patch_provider(monkeypatch, FakeProvider("reply"))
    settings = Settings(chat_persistence_enabled=True)
    chat_store = FakeChatStore()
    guest_id = uuid.uuid4()
    caller = CallerContext.anonymous(guest_id=guest_id)
    service = _service(settings, chat_store=chat_store)

    default = await service.complete_chat(_request("hi"), caller)
    assert default.session_id is not None

    # A second session belonging to the same guest_id predates the enforcement
    # (e.g. legacy data); addressing it explicitly must still be rejected.
    other_session = await chat_store.create_session(guest_id=guest_id)

    with pytest.raises(SessionNotFoundError):
        await service.complete_chat(
            _request("hi again", session_id=other_session.id), caller
        )


@pytest.mark.anyio
async def test_guest_addressing_foreign_session_id_returns_404(
    monkeypatch: MonkeyPatch,
) -> None:
    _patch_provider(monkeypatch, FakeProvider())
    settings = Settings(chat_persistence_enabled=True)
    chat_store = FakeChatStore()
    other_guest_session = await chat_store.create_session(guest_id=uuid.uuid4())
    service = _service(settings, chat_store=chat_store)
    caller = CallerContext.anonymous(guest_id=uuid.uuid4())

    with pytest.raises(SessionNotFoundError):
        await service.complete_chat(
            _request("hi", session_id=other_guest_session.id), caller
        )


@pytest.mark.anyio
async def test_authenticated_user_without_session_id_still_creates_new_session(
    monkeypatch: MonkeyPatch,
) -> None:
    """Multi-session behavior for authenticated users is unchanged by this phase."""
    _patch_provider(monkeypatch, FakeProvider("reply"))
    settings = Settings(chat_persistence_enabled=True)
    chat_store = FakeChatStore()
    service = _service(settings, chat_store=chat_store)
    caller = CallerContext.for_user(uuid.uuid4())

    first = await service.complete_chat(_request("first"), caller)
    second = await service.complete_chat(_request("second"), caller)

    assert first.session_id != second.session_id
    assert len(chat_store.sessions) == 2


# --------------------------------------------------------------------------- #
# Guest provider/model gating                                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_guest_non_default_provider_rejected_before_provider_call(
    monkeypatch: MonkeyPatch,
) -> None:
    provider = RecordingProvider()
    _patch_provider(monkeypatch, provider)
    settings = Settings(chat_persistence_enabled=True, llm_provider="openai")
    service = _service(settings)
    caller = CallerContext.anonymous(guest_id=uuid.uuid4())

    with pytest.raises(ProviderNotAllowedError):
        await service.complete_chat(_request("hi", provider="anthropic"), caller)
    assert provider.complete_calls == 0


@pytest.mark.anyio
async def test_guest_non_default_model_rejected_before_provider_call(
    monkeypatch: MonkeyPatch,
) -> None:
    """Provider omitted (defaults to system provider) but model explicitly set
    to a different provider's model — the schema's provider/model compatibility
    validator only runs when both fields are supplied, so this exercises the
    service-level gating independently of that schema guard.
    """
    provider = RecordingProvider()
    _patch_provider(monkeypatch, provider)
    settings = Settings(chat_persistence_enabled=True, llm_provider="openai")
    service = _service(settings)
    caller = CallerContext.anonymous(guest_id=uuid.uuid4())

    with pytest.raises(ProviderNotAllowedError):
        await service.complete_chat(
            _request("hi", model=settings.anthropic_model), caller
        )
    assert provider.complete_calls == 0


@pytest.mark.anyio
async def test_guest_omitting_provider_and_model_uses_system_default(
    monkeypatch: MonkeyPatch,
) -> None:
    provider = RecordingProvider("default reply")
    _patch_provider(monkeypatch, provider)
    settings = Settings(chat_persistence_enabled=True, llm_provider="openai")
    service = _service(settings)
    caller = CallerContext.anonymous(guest_id=uuid.uuid4())

    result = await service.complete_chat(_request("hi"), caller)

    assert result.content == "default reply"
    assert result.provider == "openai"
    assert provider.complete_calls == 1


@pytest.mark.anyio
async def test_guest_explicit_default_provider_and_model_is_allowed(
    monkeypatch: MonkeyPatch,
) -> None:
    provider = RecordingProvider("default reply")
    _patch_provider(monkeypatch, provider)
    settings = Settings(chat_persistence_enabled=True, llm_provider="openai")
    service = _service(settings)
    caller = CallerContext.anonymous(guest_id=uuid.uuid4())

    result = await service.complete_chat(
        _request("hi", provider="openai", model=settings.openai_model), caller
    )

    assert result.content == "default reply"
    assert provider.complete_calls == 1


@pytest.mark.anyio
async def test_authenticated_user_may_use_non_default_provider(
    monkeypatch: MonkeyPatch,
) -> None:
    provider = RecordingProvider("anthropic reply")
    _patch_provider(monkeypatch, provider)
    settings = Settings(chat_persistence_enabled=True, llm_provider="openai")
    service = _service(settings)
    caller = CallerContext.for_user(uuid.uuid4())

    result = await service.complete_chat(_request("hi", provider="anthropic"), caller)

    assert result.provider == "anthropic"
    assert provider.complete_calls == 1


@pytest.mark.anyio
async def test_guest_non_default_provider_rejected_in_stream_preflight(
    monkeypatch: MonkeyPatch,
) -> None:
    provider = RecordingProvider()
    _patch_provider(monkeypatch, provider)
    settings = Settings(chat_persistence_enabled=True, llm_provider="openai")
    service = _service(settings)
    caller = CallerContext.anonymous(guest_id=uuid.uuid4())

    with pytest.raises(ProviderNotAllowedError):
        await service.prepare_stream(_request("hi", provider="groq"), caller)
    assert provider.complete_calls == 0


# --------------------------------------------------------------------------- #
# Guest quota-remaining visibility                                            #
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_guest_quota_remaining_decreases_after_a_turn(
    monkeypatch: MonkeyPatch,
) -> None:
    _patch_provider(monkeypatch, FakeProvider("reply"))
    settings = Settings(chat_persistence_enabled=True, guest_daily_message_quota=5)
    quota_store = FakeGuestQuotaStore()
    service = _service(settings, quota_store=quota_store)
    caller = CallerContext.anonymous(guest_id=uuid.uuid4())

    before = await service.guest_quota_remaining(caller)
    await service.complete_chat(_request("hi"), caller)
    after = await service.guest_quota_remaining(caller)

    assert before == 5
    assert after == 4


@pytest.mark.anyio
async def test_guest_quota_remaining_is_none_for_authenticated_caller() -> None:
    settings = Settings(chat_persistence_enabled=True)
    service = _service(settings)
    caller = CallerContext.for_user(uuid.uuid4())

    assert await service.guest_quota_remaining(caller) is None


# --------------------------------------------------------------------------- #
# Session list (unit, fakes)                                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_list_sessions_returns_empty_when_persistence_inactive() -> None:
    service = ChatService(Settings(chat_persistence_enabled=False))

    assert await service.list_sessions(None) == []


@pytest.mark.anyio
async def test_list_sessions_returns_only_guests_default_session() -> None:
    settings = Settings(chat_persistence_enabled=True)
    chat_store = FakeChatStore()
    guest_id = uuid.uuid4()
    default = await chat_store.create_session(guest_id=guest_id)
    await chat_store.create_session(guest_id=guest_id)  # legacy extra, ignored
    service = _service(settings, chat_store=chat_store)
    caller = CallerContext.anonymous(guest_id=guest_id)

    result = await service.list_sessions(caller)

    assert [item.id for item in result] == [default.id]


@pytest.mark.anyio
async def test_list_sessions_scopes_to_owner_and_orders_by_recency(
    monkeypatch: MonkeyPatch,
) -> None:
    settings = Settings(chat_persistence_enabled=True)
    chat_store = FakeChatStore()
    user_id = uuid.uuid4()
    other_user_session = await chat_store.create_session(user_id=uuid.uuid4())
    older = await chat_store.create_session(user_id=user_id)
    newer = await chat_store.create_session(user_id=user_id)
    await chat_store.mark_last_message_at(older.id)
    await chat_store.mark_last_message_at(newer.id)
    service = _service(settings, chat_store=chat_store)
    caller = CallerContext.for_user(user_id)

    result = await service.list_sessions(caller)

    assert [item.id for item in result] == [newer.id, older.id]
    assert other_user_session.id not in [item.id for item in result]


@pytest.mark.anyio
async def test_list_sessions_includes_linked_guest_session_for_authenticated_caller() -> (
    None
):
    """Read-time projection (plan Section 2.6): a guest's default session
    surfaces for the authenticated caller it was linked to, without any
    ownership migration on the underlying row.
    """
    settings = Settings(chat_persistence_enabled=True)
    chat_store = FakeChatStore()
    user_id = uuid.uuid4()
    guest_id = uuid.uuid4()
    linked_session = await chat_store.create_session(guest_id=guest_id)
    chat_store.linked_guest_ids_by_user[user_id] = {guest_id}
    service = _service(settings, chat_store=chat_store)
    caller = CallerContext.for_user(user_id)

    result = await service.list_sessions(caller)

    assert [item.id for item in result] == [linked_session.id]
    # The row itself is untouched: still guest-owned, not migrated.
    assert linked_session.user_id is None
    assert linked_session.guest_id == guest_id


@pytest.mark.anyio
async def test_authenticated_caller_can_resume_and_append_to_linked_guest_session(
    monkeypatch: MonkeyPatch,
) -> None:
    """A linked guest session that appears in the list (previous test) must
    also be resumable and continuable — ``get_owned_session`` needs the same
    linked-guest projection as ``list_sessions_for_owner``, or the list would
    show a session the caller can't actually open (plan Sections 2.6, 5.2).
    """
    _patch_provider(monkeypatch, FakeProvider("continuing the linked chat"))
    settings = Settings(chat_persistence_enabled=True)
    chat_store = FakeChatStore()
    user_id = uuid.uuid4()
    guest_id = uuid.uuid4()
    linked_session = await chat_store.create_session(guest_id=guest_id)
    await chat_store.add_message(
        session_id=linked_session.id, seq=1, role="user", content="from before login"
    )
    chat_store.linked_guest_ids_by_user[user_id] = {guest_id}
    service = _service(settings, chat_store=chat_store)
    caller = CallerContext.for_user(user_id)

    transcript = await service.get_session_transcript(linked_session.id, caller)
    assert transcript.id == linked_session.id
    assert [m.content for m in transcript.messages] == ["from before login"]

    result = await service.complete_chat(
        _request("continuing", session_id=linked_session.id), caller
    )
    assert result.session_id == linked_session.id
    # The row is still guest-owned; appending doesn't migrate ownership.
    assert linked_session.user_id is None


# --------------------------------------------------------------------------- #
# Session create (unit, fakes)                                                #
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_authenticated_user_can_create_an_empty_session() -> None:
    settings = Settings(chat_persistence_enabled=True)
    chat_store = FakeChatStore()
    service = _service(settings, chat_store=chat_store)
    caller = CallerContext.for_user(uuid.uuid4())

    result = await service.create_session(caller)

    assert result.messages == []
    assert result.id in chat_store.sessions


@pytest.mark.anyio
async def test_guest_create_session_returns_new_chat_forbidden() -> None:
    settings = Settings(chat_persistence_enabled=True)
    service = _service(settings)
    caller = CallerContext.anonymous(guest_id=uuid.uuid4())

    with pytest.raises(NewChatForbiddenError):
        await service.create_session(caller)


@pytest.mark.anyio
async def test_create_session_without_caller_raises_session_not_found() -> None:
    service = ChatService(Settings(chat_persistence_enabled=False))

    with pytest.raises(SessionNotFoundError):
        await service.create_session(None)


# --------------------------------------------------------------------------- #
# Session delete (Phase 2)                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_delete_owned_session_removes_session_and_messages() -> None:
    settings = Settings(chat_persistence_enabled=True)
    chat_store = FakeChatStore()
    service = _service(settings, chat_store=chat_store)
    user_id = uuid.uuid4()
    caller = CallerContext.for_user(user_id)
    session = await chat_store.create_session(user_id=user_id)
    await chat_store.add_message(
        session_id=session.id, seq=1, role="user", content="hello"
    )
    await chat_store.add_summary(
        session_id=session.id,
        version=1,
        covers_through_seq=1,
        content="summary",
        provider="openai",
        model="gpt-4o-mini",
    )

    await service.delete_session(session.id, caller)

    assert session.id not in chat_store.sessions
    assert not any(m.session_id == session.id for m in chat_store.messages)
    assert not any(s.session_id == session.id for s in chat_store.summaries)


@pytest.mark.anyio
async def test_delete_foreign_session_raises_session_not_found() -> None:
    settings = Settings(chat_persistence_enabled=True)
    chat_store = FakeChatStore()
    service = _service(settings, chat_store=chat_store)
    owner_id = uuid.uuid4()
    other_session = await chat_store.create_session(user_id=uuid.uuid4())
    caller = CallerContext.for_user(owner_id)

    with pytest.raises(SessionNotFoundError):
        await service.delete_session(other_session.id, caller)


@pytest.mark.anyio
async def test_guest_delete_session_raises_new_chat_forbidden() -> None:
    settings = Settings(chat_persistence_enabled=True)
    chat_store = FakeChatStore()
    service = _service(settings, chat_store=chat_store)
    guest_id = uuid.uuid4()
    session = await chat_store.create_session(guest_id=guest_id)
    caller = CallerContext.anonymous(guest_id=guest_id)

    with pytest.raises(NewChatForbiddenError):
        await service.delete_session(session.id, caller)


@pytest.mark.anyio
async def test_delete_linked_guest_session_succeeds_for_authenticated_user() -> None:
    settings = Settings(chat_persistence_enabled=True)
    chat_store = FakeChatStore()
    service = _service(settings, chat_store=chat_store)
    user_id = uuid.uuid4()
    guest_id = uuid.uuid4()
    chat_store.linked_guest_ids_by_user[user_id] = {guest_id}
    linked_session = await chat_store.create_session(guest_id=guest_id)
    caller = CallerContext.for_user(user_id)

    await service.delete_session(linked_session.id, caller)

    assert linked_session.id not in chat_store.sessions


@pytest.mark.anyio
async def test_delete_session_without_caller_raises_session_not_found() -> None:
    settings = Settings(chat_persistence_enabled=True)
    service = _service(settings)

    with pytest.raises(SessionNotFoundError):
        await service.delete_session(uuid.uuid4(), None)


# --------------------------------------------------------------------------- #
# Session list/create integration against real Postgres (skips when unavailable) #
# --------------------------------------------------------------------------- #
# The ``db_session`` fixture is provided by tests/conftest.py.


@pytest.mark.anyio
async def test_sql_list_sessions_includes_linked_guest_session(db_session) -> None:
    chat_store = SqlChatStore(db_session)
    user_store = SqlUserStore(db_session)
    guest_store = SqlGuestStore(db_session)

    user = await user_store.create(
        sub=f"sql-list-sessions-{uuid.uuid4()}", email=None, name=None, picture=None
    )
    guest = await guest_store.create(token_hash=f"hash-{uuid.uuid4()}")
    own_session = await chat_store.create_session(user_id=user.id)
    linked_session = await chat_store.create_session(guest_id=guest.id)
    unrelated_guest = await guest_store.create(token_hash=f"hash-{uuid.uuid4()}")
    await chat_store.create_session(guest_id=unrelated_guest.id)
    await db_session.flush()

    await guest_store.link_to_user(guest.id, user.id)
    await db_session.flush()

    result = await chat_store.list_sessions_for_owner(user_id=user.id)

    result_ids = {session.id for session in result}
    assert own_session.id in result_ids
    assert linked_session.id in result_ids
    assert len(result_ids) == 2
