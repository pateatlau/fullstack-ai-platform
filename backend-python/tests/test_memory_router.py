"""Memory REST API integration tests (Epic 05 Phase 7)."""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Iterator

import pytest
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI
from sqlalchemy import text

from app.ai.deps import get_conversation_summary_service, get_memory_manager
from app.ai.memory.lifecycle import LifecycleState
from app.ai.memory.lifecycle_manager import LifecycleManager
from app.ai.memory.manager import MemoryManager
from app.ai.memory.models import MemoryRecord, MemoryScope, MemoryType
from app.ai.memory.providers.pgvector import PgVectorMemoryProvider
from app.ai.memory.summarizer import ConversationSummaryService
from app.ai.prompts.manager import create_prompt_manager
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.security import create_access_token
from app.db.chat import SqlChatStore
from app.db.identity import SqlUserStore
from app.db.session import get_db_session
from app.routers import memory as memory_router

DIMENSIONS = 1536
_NOW = datetime.datetime.now(datetime.timezone.utc)


def _bind_db_session(test_app, session) -> None:
    async def _override_db_session():
        yield session

    test_app.dependency_overrides[get_db_session] = _override_db_session


def _clear_router_overrides(test_app) -> None:
    test_app.dependency_overrides.pop(get_memory_manager, None)
    test_app.dependency_overrides.pop(get_conversation_summary_service, None)
    test_app.dependency_overrides.pop(get_db_session, None)


def _embedding(seed: float = 0.1) -> list[float]:
    return [seed] * DIMENSIONS


def _auth_headers(user_id: uuid.UUID) -> dict[str, str]:
    token = create_access_token(user_id=user_id, settings=get_settings())
    return {"Authorization": f"Bearer {token}"}


async def _make_user(session) -> uuid.UUID:
    user = await SqlUserStore(session).create(
        sub=f"memory-api-{uuid.uuid4()}",
        email=None,
        name=None,
        picture=None,
    )
    return user.id


async def _make_session(session, *, user_id: uuid.UUID) -> uuid.UUID:
    chat_session = await SqlChatStore(session).create_session(user_id=user_id)
    return chat_session.id


def _memory_settings() -> Settings:
    return Settings(openai_api_key="test-key", memory_enabled=True)


def _build_test_app() -> FastAPI:
    """Isolated app mirroring main.py: memory router always mounted."""
    test_app = FastAPI()
    test_app.include_router(memory_router.router)
    register_exception_handlers(test_app)
    return test_app


async def _pgvector_available(session) -> bool:
    try:
        result = await session.scalar(
            text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        )
        return result == 1
    except Exception:
        return False


@pytest.fixture
async def memory_db_session(db_session):
    if not await _pgvector_available(db_session):
        pytest.skip("pgvector extension is not available")
    return db_session


@pytest.fixture
def memory_api_app(monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("MEMORY_ENABLED", "true")
    get_settings.cache_clear()
    yield _build_test_app()
    get_settings.cache_clear()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path",
    [
        "/api/memory/preferences",
        "/api/memory/records?memory_type=user",
    ],
)
async def test_memory_api_disabled_returns_503(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    user_id = await _make_user(db_session)
    headers = _auth_headers(user_id)
    monkeypatch.setenv("MEMORY_ENABLED", "false")
    get_settings.cache_clear()
    test_app = _build_test_app()
    _bind_db_session(test_app, db_session)

    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://testserver",
    ) as client:
        response = await client.get(path, headers=headers)

    get_settings.cache_clear()
    assert response.status_code == 503
    assert response.status_code != 404
    body = response.json()
    assert body["error"]["code"] == "feature_disabled"
    assert body["error"]["message"] == "Memory is not enabled on this server."


@pytest.mark.anyio
async def test_memory_api_requires_authentication(
    db_session,
    memory_api_app: FastAPI,
) -> None:
    _bind_db_session(memory_api_app, db_session)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=memory_api_app),
            base_url="http://testserver",
        ) as client:
            response = await client.get("/api/memory/preferences")
    finally:
        _clear_router_overrides(memory_api_app)

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


@pytest.mark.anyio
async def test_list_project_records_rejects_missing_or_nil_session_id(
    db_session,
    memory_api_app: FastAPI,
) -> None:
    user_id = await _make_user(db_session)
    _bind_db_session(memory_api_app, db_session)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=memory_api_app),
            base_url="http://testserver",
        ) as client:
            missing_session = await client.get(
                "/api/memory/records?memory_type=project",
                headers=_auth_headers(user_id),
            )
            nil_session = await client.get(
                "/api/memory/records",
                params={
                    "memory_type": "project",
                    "session_id": str(uuid.UUID(int=0)),
                },
                headers=_auth_headers(user_id),
            )
    finally:
        _clear_router_overrides(memory_api_app)

    assert missing_session.status_code == 422
    assert missing_session.json()["error"]["code"] == "validation_error"
    assert nil_session.status_code == 422
    assert nil_session.json()["error"]["code"] == "validation_error"


@pytest.mark.anyio
async def test_list_and_get_memory_records(
    memory_db_session,
    memory_api_app: FastAPI,
) -> None:
    user_id = await _make_user(memory_db_session)
    provider = PgVectorMemoryProvider(memory_db_session, _memory_settings())
    created = await provider.create_record(
        MemoryRecord(
            id=uuid.uuid4(),
            memory_type=MemoryType.USER,
            scope=MemoryScope.USER,
            owner_id=user_id,
            content="Prefers concise answers.",
            embedding=_embedding(),
            created_at=_NOW,
            updated_at=_NOW,
            lifecycle_state=LifecycleState.ACTIVE,
            source="api",
        )
    )
    await memory_db_session.commit()

    def override_manager() -> MemoryManager:
        settings = _memory_settings()
        return MemoryManager(
            provider=provider,
            settings=settings,
            lifecycle_manager=LifecycleManager(provider, settings=settings),
        )

    _bind_db_session(memory_api_app, memory_db_session)
    memory_api_app.dependency_overrides[get_memory_manager] = override_manager
    try:
        async with AsyncClient(
            transport=ASGITransport(app=memory_api_app),
            base_url="http://testserver",
        ) as client:
            list_response = await client.get(
                "/api/memory/records?memory_type=user",
                headers=_auth_headers(user_id),
            )
            get_response = await client.get(
                f"/api/memory/records/{created.id}",
                headers=_auth_headers(user_id),
            )
    finally:
        _clear_router_overrides(memory_api_app)

    assert list_response.status_code == 200
    body = list_response.json()
    assert len(body["records"]) == 1
    assert body["records"][0]["content"] == "Prefers concise answers."
    assert "lifecycle_state" not in body["records"][0]
    assert "embedding" not in body["records"][0]

    assert get_response.status_code == 200
    assert get_response.json()["id"] == str(created.id)


@pytest.mark.anyio
async def test_delete_memory_record_soft_deletes(
    memory_db_session,
    memory_api_app: FastAPI,
) -> None:
    user_id = await _make_user(memory_db_session)
    provider = PgVectorMemoryProvider(memory_db_session, _memory_settings())
    created = await provider.create_record(
        MemoryRecord(
            id=uuid.uuid4(),
            memory_type=MemoryType.USER,
            scope=MemoryScope.USER,
            owner_id=user_id,
            content="Delete me.",
            embedding=_embedding(),
            created_at=_NOW,
            updated_at=_NOW,
            lifecycle_state=LifecycleState.ACTIVE,
            source="api",
        )
    )
    await memory_db_session.commit()

    def override_manager() -> MemoryManager:
        settings = _memory_settings()
        return MemoryManager(
            provider=provider,
            settings=settings,
            lifecycle_manager=LifecycleManager(provider, settings=settings),
        )

    _bind_db_session(memory_api_app, memory_db_session)
    memory_api_app.dependency_overrides[get_memory_manager] = override_manager
    try:
        async with AsyncClient(
            transport=ASGITransport(app=memory_api_app),
            base_url="http://testserver",
        ) as client:
            delete_response = await client.delete(
                f"/api/memory/records/{created.id}",
                headers=_auth_headers(user_id),
            )
            get_response = await client.get(
                f"/api/memory/records/{created.id}",
                headers=_auth_headers(user_id),
            )
    finally:
        _clear_router_overrides(memory_api_app)

    assert delete_response.status_code == 204
    assert get_response.status_code == 404


@pytest.mark.anyio
async def test_preference_routes_round_trip(
    memory_db_session,
    memory_api_app: FastAPI,
) -> None:
    user_id = await _make_user(memory_db_session)
    provider = PgVectorMemoryProvider(memory_db_session, _memory_settings())

    def override_manager() -> MemoryManager:
        settings = _memory_settings()
        return MemoryManager(
            provider=provider,
            settings=settings,
            lifecycle_manager=LifecycleManager(provider, settings=settings),
        )

    _bind_db_session(memory_api_app, memory_db_session)
    memory_api_app.dependency_overrides[get_memory_manager] = override_manager
    try:
        async with AsyncClient(
            transport=ASGITransport(app=memory_api_app),
            base_url="http://testserver",
        ) as client:
            upsert = await client.put(
                "/api/memory/preferences/response_tone",
                headers=_auth_headers(user_id),
                json={"value": {"tone": "concise"}},
            )
            listed = await client.get(
                "/api/memory/preferences",
                headers=_auth_headers(user_id),
            )
            deleted = await client.delete(
                "/api/memory/preferences/response_tone",
                headers=_auth_headers(user_id),
            )
    finally:
        _clear_router_overrides(memory_api_app)

    assert upsert.status_code == 200
    assert upsert.json() == {"key": "response_tone", "value": {"tone": "concise"}}
    assert listed.status_code == 200
    assert listed.json()["preferences"] == [
        {"key": "response_tone", "value": {"tone": "concise"}}
    ]
    assert deleted.status_code == 204


@pytest.mark.anyio
async def test_clear_session_summary(
    memory_db_session,
    memory_api_app: FastAPI,
) -> None:
    user_id = await _make_user(memory_db_session)
    session_id = await _make_session(memory_db_session, user_id=user_id)
    chat_store = SqlChatStore(memory_db_session)
    await chat_store.add_summary(
        session_id=session_id,
        version=1,
        covers_through_seq=3,
        content="Summary to clear.",
        provider="openai",
        model="gpt-test",
    )
    await memory_db_session.commit()

    provider = PgVectorMemoryProvider(memory_db_session, _memory_settings())

    def override_manager() -> MemoryManager:
        settings = _memory_settings()
        return MemoryManager(
            provider=provider,
            settings=settings,
            lifecycle_manager=LifecycleManager(provider, settings=settings),
        )

    _bind_db_session(memory_api_app, memory_db_session)
    memory_api_app.dependency_overrides[get_memory_manager] = override_manager
    memory_api_app.dependency_overrides[get_conversation_summary_service] = lambda: (
        ConversationSummaryService(
            chat_store=chat_store,
            prompt_manager=create_prompt_manager(),
        )
    )
    try:
        async with AsyncClient(
            transport=ASGITransport(app=memory_api_app),
            base_url="http://testserver",
        ) as client:
            response = await client.delete(
                f"/api/memory/sessions/{session_id}/summary",
                headers=_auth_headers(user_id),
            )
    finally:
        _clear_router_overrides(memory_api_app)

    assert response.status_code == 204
    assert await chat_store.get_latest_summary(session_id) is None
