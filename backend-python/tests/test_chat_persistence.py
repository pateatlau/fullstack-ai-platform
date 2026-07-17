"""Phase 5 tests: chat lifecycle persistence (guest + auth).

Unit tests drive ChatService with in-memory fakes (no DB). Integration tests
exercise the real endpoints against the compose Postgres and skip when the DB is
unavailable (the ``db_session`` fixture from conftest provides the skip guard).
"""

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.caller import CallerContext
from app.core.config import Settings, get_settings
from app.main import app
from app.providers.factory import ProviderFactory
from app.schemas.chat import ChatMessageSchema, ChatRequestSchema
from app.services.chat_service import (
    ChatService,
    ChatServiceError,
    SessionNotFoundError,
)
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

    async def complete_chat(self, messages, model, temperature=0.7):
        self.complete_calls += 1
        return await super().complete_chat(messages, model, temperature)


class BoomProvider(FakeProvider):
    async def complete_chat(self, messages, model, temperature=0.7):
        raise RuntimeError("provider exploded")


def _patch_provider(monkeypatch, provider) -> None:
    def get_provider(name=None, settings=None):
        return provider

    monkeypatch.setattr(ProviderFactory, "get_provider", staticmethod(get_provider))


def _service(provider_settings: Settings, **overrides) -> ChatService:
    chat_store = overrides.get("chat_store", FakeChatStore())
    usage_store = overrides.get("usage_store", FakeUsageStore())
    quota_store = overrides.get("quota_store", FakeGuestQuotaStore())
    return ChatService(
        provider_settings,
        chat_store=chat_store,
        usage_store=usage_store,
        quota_service=QuotaService(store=quota_store, settings=provider_settings),
    )


def _request(content: str, **kwargs) -> ChatRequestSchema:
    return ChatRequestSchema(
        messages=[ChatMessageSchema(role="user", content=content)], **kwargs
    )


# --------------------------------------------------------------------------- #
# ChatService persistence (unit, fakes)                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_complete_persists_new_session_messages_and_usage(monkeypatch) -> None:
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
async def test_guest_over_quota_blocks_provider_call(monkeypatch) -> None:
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
async def test_append_to_unowned_session_raises_404(monkeypatch) -> None:
    _patch_provider(monkeypatch, FakeProvider())
    settings = Settings(chat_persistence_enabled=True)
    chat_store = FakeChatStore()
    owner = await chat_store.create_session(user_id=uuid.uuid4())
    service = _service(settings, chat_store=chat_store)
    other_caller = CallerContext.for_user(uuid.uuid4())

    with pytest.raises(SessionNotFoundError):
        await service.complete_chat(_request("hi", session_id=owner.id), other_caller)


@pytest.mark.anyio
async def test_client_message_id_makes_append_idempotent(monkeypatch) -> None:
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
async def test_provider_failure_persists_error_assistant(monkeypatch) -> None:
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
async def test_stateless_when_persistence_inactive(monkeypatch) -> None:
    # No caller supplied -> stateless path, nothing persisted.
    _patch_provider(monkeypatch, FakeProvider("stateless"))
    settings = Settings(chat_persistence_enabled=True)
    chat_store = FakeChatStore()
    service = _service(settings, chat_store=chat_store)

    result = await service.complete_chat(_request("hi"), None)

    assert result.session_id is None
    assert result.content == "stateless"
    assert chat_store.sessions == {}


# --------------------------------------------------------------------------- #
# Endpoint integration (real Postgres; skips when unavailable)                 #
# --------------------------------------------------------------------------- #


@pytest.fixture
async def app_persistence(db_session, monkeypatch):
    """Enable persistence, mock the provider, and reset the app engine after."""
    monkeypatch.setenv("CHAT_PERSISTENCE_ENABLED", "true")
    get_settings.cache_clear()
    provider = FakeProvider("Integration reply")

    def get_provider(name=None, settings=None):
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
async def test_chat_endpoint_persists_and_resumes(app_persistence) -> None:
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
async def test_chat_endpoint_enforces_guest_quota(app_persistence, monkeypatch) -> None:
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
async def test_resume_unknown_session_returns_404(app_persistence) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        resp = await client.get(f"/api/chat/sessions/{uuid.uuid4()}")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "session_not_found"


@pytest.mark.anyio
async def test_readiness_reports_ok(app_persistence) -> None:
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
async def test_stream_endpoint_persists_and_resumes(app_persistence) -> None:
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
