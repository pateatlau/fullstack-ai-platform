"""Phase 5 tests: chat lifecycle persistence (guest + auth).

Unit tests drive ChatService with in-memory fakes (no DB). Integration tests
exercise the real endpoints against the compose Postgres and skip when the DB is
unavailable (the ``db_session`` fixture from conftest provides the skip guard).
"""

import datetime
import uuid
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from pytest import MonkeyPatch
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette.types import Message, Scope

from app.core.caller import CallerContext
from app.core.config import Settings, get_settings
from app.core.security import create_access_token
from app.db.chat import SqlChatStore
from app.db.identity import SqlUserStore
from app.db.models import ChatMessage, SessionSummary, UsageEvent
from app.main import app
from app.providers.base import ProviderCompletion
from app.providers.factory import ProviderFactory
from app.schemas.chat import ChatMessageSchema, ChatRequestSchema, ProviderName
from app.services.chat_service import (
    ChatService,
    ChatServiceError,
    ChatStore,
    SessionNotFoundError,
    UsageStore,
)
from app.services.quota_service import GuestQuotaStore
from app.services.quota_service import QuotaExceededError, QuotaService
from tests.fakes import (
    FakeChatStore,
    FakeGuestQuotaStore,
    FakeProvider,
    FakeUsageStore,
)


class RecordingProvider(FakeProvider):
    """Fake provider that counts how many times completion is requested."""

    def __init__(self, response: str = "recorded") -> None:
        super().__init__(response)
        self.complete_calls = 0

    async def complete_chat(
        self,
        messages: list[ChatMessageSchema],
        model: str,
        temperature: float = 0.7,
        *,
        max_tokens: int | None = None,
    ) -> ProviderCompletion:
        self.complete_calls += 1
        return await super().complete_chat(
            messages, model, temperature, max_tokens=max_tokens
        )


class BoomProvider(FakeProvider):
    async def complete_chat(
        self,
        messages: list[ChatMessageSchema],
        model: str,
        temperature: float = 0.7,
        *,
        max_tokens: int | None = None,
    ) -> ProviderCompletion:
        del messages, model, temperature, max_tokens
        raise RuntimeError("provider exploded")


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
        # Keep the connection logically open for stream tests.
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


def _service(
    provider_settings: Settings,
    *,
    chat_store: ChatStore | None = None,
    usage_store: UsageStore | None = None,
    quota_store: GuestQuotaStore | None = None,
) -> ChatService:
    resolved_chat_store = chat_store or FakeChatStore()
    resolved_usage_store = usage_store or FakeUsageStore()
    resolved_quota_store = quota_store or FakeGuestQuotaStore()
    return ChatService(
        provider_settings,
        chat_store=resolved_chat_store,
        usage_store=resolved_usage_store,
        quota_service=QuotaService(
            store=resolved_quota_store, settings=provider_settings
        ),
    )


def _request(
    content: str,
    *,
    model: str | None = None,
    provider: ProviderName | None = None,
    temperature: float = 0.7,
    session_id: uuid.UUID | None = None,
    client_message_id: str | None = None,
) -> ChatRequestSchema:
    return ChatRequestSchema(
        messages=[ChatMessageSchema(role="user", content=content)],
        model=model,
        provider=provider,
        temperature=temperature,
        session_id=session_id,
        client_message_id=client_message_id,
    )


# --------------------------------------------------------------------------- #
# ChatService persistence (unit, fakes)                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_complete_persists_new_session_messages_and_usage(
    monkeypatch: MonkeyPatch,
) -> None:
    _patch_provider(monkeypatch, FakeProvider("Persisted reply"))
    settings = Settings(chat_persistence_enabled=True, llm_provider="openai")
    chat_store = FakeChatStore()
    usage_store = FakeUsageStore()
    service = _service(settings, chat_store=chat_store, usage_store=usage_store)
    caller = CallerContext.for_user(uuid.uuid4())

    result = await service.complete_chat(_request("hi"), caller)

    assert result.session_id is not None
    assert result.content == "Persisted reply"
    messages = await chat_store.list_messages(result.session_id)
    assert [(m.seq, m.role) for m in messages] == [(1, "user"), (2, "assistant")]
    assert messages[1].provider == "openai"
    assert len(usage_store.events) == 1
    assert usage_store.events[0].token_source == "provider_reported"


@pytest.mark.anyio
async def test_guest_over_quota_blocks_provider_call(
    monkeypatch: MonkeyPatch,
) -> None:
    provider = RecordingProvider()
    _patch_provider(monkeypatch, provider)
    settings = Settings(chat_persistence_enabled=True, guest_daily_message_quota=1)
    quota_store = FakeGuestQuotaStore()
    guest_id = uuid.uuid4()
    quota = QuotaService(store=quota_store, settings=settings)
    await quota.record(guest_id)  # counter now at the limit (1)

    service = ChatService(
        settings,
        chat_store=FakeChatStore(),
        usage_store=FakeUsageStore(),
        quota_service=quota,
    )
    caller = CallerContext.anonymous(guest_id=guest_id)

    with pytest.raises(QuotaExceededError):
        await service.complete_chat(_request("hi"), caller)
    assert provider.complete_calls == 0  # rejected before any provider call


@pytest.mark.anyio
async def test_append_to_unowned_session_raises_404(
    monkeypatch: MonkeyPatch,
) -> None:
    _patch_provider(monkeypatch, FakeProvider())
    settings = Settings(chat_persistence_enabled=True)
    chat_store = FakeChatStore()
    owner = await chat_store.create_session(user_id=uuid.uuid4())
    service = _service(settings, chat_store=chat_store)
    other_caller = CallerContext.for_user(uuid.uuid4())

    with pytest.raises(SessionNotFoundError):
        await service.complete_chat(_request("hi", session_id=owner.id), other_caller)


@pytest.mark.anyio
async def test_client_message_id_makes_append_idempotent(
    monkeypatch: MonkeyPatch,
) -> None:
    provider = RecordingProvider("first reply")
    _patch_provider(monkeypatch, provider)
    settings = Settings(chat_persistence_enabled=True)
    chat_store = FakeChatStore()
    service = _service(settings, chat_store=chat_store)
    caller = CallerContext.for_user(uuid.uuid4())

    first = await service.complete_chat(
        _request("hi", client_message_id="cm-1"), caller
    )
    replay = await service.complete_chat(
        _request("hi", session_id=first.session_id, client_message_id="cm-1"), caller
    )

    assert replay.content == first.content
    assert provider.complete_calls == 1  # replay did not call the provider again


@pytest.mark.anyio
async def test_provider_failure_persists_error_assistant(
    monkeypatch: MonkeyPatch,
) -> None:
    _patch_provider(monkeypatch, BoomProvider())
    settings = Settings(chat_persistence_enabled=True)
    chat_store = FakeChatStore()
    service = _service(settings, chat_store=chat_store)
    caller = CallerContext.for_user(uuid.uuid4())

    with pytest.raises(ChatServiceError):
        await service.complete_chat(_request("hi"), caller)

    sessions = list(chat_store.sessions.values())
    assert len(sessions) == 1
    messages = await chat_store.list_messages(sessions[0].id)
    assert [(m.role, m.status) for m in messages] == [
        ("user", "complete"),
        ("assistant", "error"),
    ]


@pytest.mark.anyio
async def test_stateless_when_persistence_inactive(
    monkeypatch: MonkeyPatch,
) -> None:
    # No caller supplied -> stateless path, nothing persisted.
    _patch_provider(monkeypatch, FakeProvider("stateless"))
    settings = Settings(chat_persistence_enabled=True)
    chat_store = FakeChatStore()
    service = _service(settings, chat_store=chat_store)

    result = await service.complete_chat(_request("hi"), None)

    assert result.session_id is None
    assert result.content == "stateless"
    assert chat_store.sessions == {}


@pytest.mark.anyio
async def test_persistence_rejects_request_not_ending_in_user_message(
    monkeypatch: MonkeyPatch,
) -> None:
    _patch_provider(monkeypatch, FakeProvider("should not be used"))
    settings = Settings(chat_persistence_enabled=True)
    service = _service(
        settings, chat_store=FakeChatStore(), usage_store=FakeUsageStore()
    )
    caller = CallerContext.for_user(uuid.uuid4())
    request = ChatRequestSchema(
        messages=[
            ChatMessageSchema(role="user", content="hi"),
            ChatMessageSchema(role="assistant", content="previous response"),
        ]
    )

    with pytest.raises(ChatServiceError) as exc_info:
        await service.complete_chat(request, caller)

    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "validation_error"
    assert "last message" in exc_info.value.message.lower()


# --------------------------------------------------------------------------- #
# Endpoint integration (real Postgres; skips when unavailable)                 #
# --------------------------------------------------------------------------- #


@pytest.fixture
async def app_persistence(
    db_session: AsyncSession,
    monkeypatch: MonkeyPatch,
) -> AsyncIterator[FakeProvider]:
    """Enable persistence, mock the provider, and reset the app engine after."""
    _ = db_session
    monkeypatch.setenv("CHAT_PERSISTENCE_ENABLED", "true")
    get_settings.cache_clear()
    provider = FakeProvider("Integration reply")

    def get_provider(
        name: ProviderName | None = None,
        settings: Settings | None = None,
    ) -> FakeProvider:
        _ = name, settings
        return provider

    monkeypatch.setattr(ProviderFactory, "get_provider", staticmethod(get_provider))
    try:
        yield provider
    finally:
        get_settings.cache_clear()
        from app.db.engine import get_engine, get_sessionmaker

        await get_engine().dispose()
        get_engine.cache_clear()
        get_sessionmaker.cache_clear()


@pytest.mark.anyio
async def test_chat_endpoint_persists_and_resumes(
    app_persistence: FakeProvider,
) -> None:
    _ = app_persistence
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        created = await client.post(
            "/api/chat", json={"messages": [{"role": "user", "content": "Hello DB"}]}
        )
        assert created.status_code == 200
        body = created.json()
        session_id = body["session_id"]
        assert session_id is not None
        guest_token = created.headers.get("X-Guest-Token")
        assert guest_token  # a fresh guest identity was issued

        resumed = await client.get(
            f"/api/chat/sessions/{session_id}",
            headers={"X-Guest-Token": guest_token},
        )

    assert resumed.status_code == 200
    transcript = resumed.json()
    assert [m["role"] for m in transcript["messages"]] == ["user", "assistant"]
    assert transcript["messages"][0]["content"] == "Hello DB"
    assert transcript["messages"][1]["content"] == "Integration reply"


@pytest.mark.anyio
async def test_chat_endpoint_enforces_guest_quota(
    app_persistence: FakeProvider,
    monkeypatch: MonkeyPatch,
) -> None:
    _ = app_persistence
    monkeypatch.setenv("GUEST_DAILY_MESSAGE_QUOTA", "1")
    get_settings.cache_clear()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        first = await client.post(
            "/api/chat", json={"messages": [{"role": "user", "content": "one"}]}
        )
        assert first.status_code == 200
        token = first.headers["X-Guest-Token"]

        second = await client.post(
            "/api/chat",
            json={"messages": [{"role": "user", "content": "two"}]},
            headers={"X-Guest-Token": token},
        )

    assert second.status_code == 429
    assert second.json()["error"]["code"] == "quota_exceeded"


@pytest.mark.anyio
async def test_resume_unknown_session_returns_404(
    app_persistence: FakeProvider,
) -> None:
    _ = app_persistence
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.get(f"/api/chat/sessions/{uuid.uuid4()}")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "session_not_found"


@pytest.mark.anyio
async def test_readiness_reports_ok(app_persistence: FakeProvider) -> None:
    _ = app_persistence
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.get("/api/health/ready")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "db": "ok"}


def _session_id_from_sse(body: str) -> str:
    import json

    for block in body.split("\n\n"):
        if block.startswith("event: start"):
            data_line = next(
                line for line in block.splitlines() if line.startswith("data: ")
            )
            return json.loads(data_line[len("data: ") :])["session_id"]
    raise AssertionError("no start frame in SSE stream")


@pytest.mark.anyio
async def test_stream_endpoint_persists_and_resumes(
    app_persistence: FakeProvider,
) -> None:
    _ = app_persistence
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        body = ""
        async with client.stream(
            "POST",
            "/api/chat/stream",
            json={"messages": [{"role": "user", "content": "Stream hi"}]},
        ) as resp:
            assert resp.status_code == 200
            guest_token = resp.headers.get("X-Guest-Token")
            async for chunk in resp.aiter_text():
                body += chunk

        assert "event: start" in body and "event: end" in body
        assert guest_token
        session_id = _session_id_from_sse(body)

        resumed = await client.get(
            f"/api/chat/sessions/{session_id}",
            headers={"X-Guest-Token": guest_token},
        )

    assert resumed.status_code == 200
    transcript = resumed.json()
    assert [m["role"] for m in transcript["messages"]] == ["user", "assistant"]
    assert transcript["messages"][0]["content"] == "Stream hi"
    assert transcript["messages"][1]["content"] == "Integration reply"


@pytest.mark.anyio
async def test_stream_records_guest_quota_tokens(
    monkeypatch: MonkeyPatch,
) -> None:
    _patch_provider(monkeypatch, FakeProvider("streamed quota reply"))
    settings = Settings(chat_persistence_enabled=True, llm_provider="openai")
    chat_store = FakeChatStore()
    usage_store = FakeUsageStore()
    quota_store = FakeGuestQuotaStore()
    service = _service(
        settings,
        chat_store=chat_store,
        usage_store=usage_store,
        quota_store=quota_store,
    )
    caller = CallerContext.anonymous(guest_id=uuid.uuid4())
    request = _request("stream quota")

    prep = await service.prepare_stream(request, caller)
    assert prep is not None

    async for _ in service.stream_chat(
        request,
        _connected_request(),
        caller=caller,
        prep=prep,
    ):
        pass

    assert caller.guest_id is not None
    assert len(usage_store.events) == 1
    window = datetime.datetime.now(datetime.timezone.utc).date()
    key = (caller.guest_id, window)
    assert quota_store.counters[key] == 1
    assert quota_store.token_totals[key] == usage_store.events[0].total_tokens


def _auth_headers(user_id: uuid.UUID) -> dict[str, str]:
    token = create_access_token(user_id=user_id, settings=get_settings())
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.anyio
async def test_delete_owned_session_returns_204_and_subsequent_get_404(
    app_persistence: FakeProvider,
    db_session: AsyncSession,
) -> None:
    _ = app_persistence
    user_store = SqlUserStore(db_session)
    user = await user_store.create(
        sub=f"delete-owned-{uuid.uuid4()}", email=None, name=None, picture=None
    )
    chat_store = SqlChatStore(db_session)
    session = await chat_store.create_session(user_id=user.id)
    await chat_store.add_message(
        session_id=session.id, seq=1, role="user", content="hello"
    )
    await db_session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        deleted = await client.delete(
            f"/api/chat/sessions/{session.id}",
            headers=_auth_headers(user.id),
        )
        assert deleted.status_code == 204

        resumed = await client.get(
            f"/api/chat/sessions/{session.id}",
            headers=_auth_headers(user.id),
        )

    assert resumed.status_code == 404
    assert resumed.json()["error"]["code"] == "session_not_found"


@pytest.mark.anyio
async def test_post_empty_session_then_first_chat_turn_sets_title(
    app_persistence: FakeProvider,
    db_session: AsyncSession,
) -> None:
    """Phase 3: POST empty session keeps title null; first chat turn sets title."""
    _ = app_persistence
    user_store = SqlUserStore(db_session)
    user = await user_store.create(
        sub=f"title-{uuid.uuid4()}", email=None, name=None, picture=None
    )
    await db_session.commit()
    headers = _auth_headers(user.id)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        created = await client.post("/api/chat/sessions", headers=headers)
        assert created.status_code == 201
        session_id = created.json()["id"]
        assert created.json()["title"] is None

        chat = await client.post(
            "/api/chat",
            json={
                "messages": [{"role": "user", "content": "Integration title message"}],
                "session_id": session_id,
            },
            headers=headers,
        )
        assert chat.status_code == 200

        listed = await client.get("/api/chat/sessions", headers=headers)
        resumed = await client.get(f"/api/chat/sessions/{session_id}", headers=headers)

    assert listed.status_code == 200
    listed_item = next(item for item in listed.json() if item["id"] == session_id)
    assert listed_item["title"] == "Integration title message"
    assert resumed.status_code == 200
    assert resumed.json()["title"] == "Integration title message"


@pytest.mark.anyio
async def test_delete_foreign_session_returns_404(
    app_persistence: FakeProvider,
    db_session: AsyncSession,
) -> None:
    _ = app_persistence
    user_store = SqlUserStore(db_session)
    owner = await user_store.create(
        sub=f"delete-owner-{uuid.uuid4()}", email=None, name=None, picture=None
    )
    other = await user_store.create(
        sub=f"delete-other-{uuid.uuid4()}", email=None, name=None, picture=None
    )
    chat_store = SqlChatStore(db_session)
    foreign_session = await chat_store.create_session(user_id=other.id)
    await db_session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.delete(
            f"/api/chat/sessions/{foreign_session.id}",
            headers=_auth_headers(owner.id),
        )

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "session_not_found"


@pytest.mark.anyio
async def test_guest_delete_session_returns_403(
    app_persistence: FakeProvider,
) -> None:
    _ = app_persistence
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        created = await client.post(
            "/api/chat", json={"messages": [{"role": "user", "content": "Guest chat"}]}
        )
        assert created.status_code == 200
        session_id = created.json()["session_id"]
        guest_token = created.headers["X-Guest-Token"]

        deleted = await client.delete(
            f"/api/chat/sessions/{session_id}",
            headers={"X-Guest-Token": guest_token},
        )

    assert deleted.status_code == 403
    assert deleted.json()["error"]["code"] == "new_chat_forbidden"


@pytest.mark.anyio
async def test_delete_session_cascades_messages_summaries_and_usage_events(
    app_persistence: FakeProvider,
    db_session: AsyncSession,
) -> None:
    _ = app_persistence
    user_store = SqlUserStore(db_session)
    user = await user_store.create(
        sub=f"delete-cascade-{uuid.uuid4()}", email=None, name=None, picture=None
    )
    chat_store = SqlChatStore(db_session)
    session = await chat_store.create_session(user_id=user.id)
    message = await chat_store.add_message(
        session_id=session.id, seq=1, role="user", content="cascade test"
    )
    await chat_store.add_summary(
        session_id=session.id,
        version=1,
        covers_through_seq=1,
        content="summary text",
        provider="openai",
        model="gpt-4o-mini",
    )
    usage = UsageEvent(
        session_id=session.id,
        user_id=user.id,
        provider="openai",
        model="gpt-4o-mini",
        token_source="provider_reported",
        kind="chat",
        message_id=message.id,
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
    )
    db_session.add(usage)
    await db_session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        deleted = await client.delete(
            f"/api/chat/sessions/{session.id}",
            headers=_auth_headers(user.id),
        )
        assert deleted.status_code == 204

    message_count = await db_session.scalar(
        select(func.count())
        .select_from(ChatMessage)
        .where(ChatMessage.session_id == session.id)
    )
    summary_count = await db_session.scalar(
        select(func.count())
        .select_from(SessionSummary)
        .where(SessionSummary.session_id == session.id)
    )
    usage_count = await db_session.scalar(
        select(func.count())
        .select_from(UsageEvent)
        .where(UsageEvent.session_id == session.id)
    )
    assert message_count == 0
    assert summary_count == 0
    assert usage_count == 0
