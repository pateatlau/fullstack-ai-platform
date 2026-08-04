"""Tests for PgVectorMemoryProvider persistence (Epic 05 Phase 3)."""

from __future__ import annotations

import datetime
import uuid
from unittest.mock import AsyncMock

import pytest

from app.ai.memory.exceptions import MemoryNotFoundError
from app.ai.memory.interfaces import MemoryProvider
from app.ai.memory.lifecycle import LifecycleState
from app.ai.memory.models import MemoryRecord, MemoryScope, MemoryType
from app.ai.memory.providers.pgvector import PgVectorMemoryProvider
from app.core.config import Settings
from app.db.chat import SqlChatStore
from app.db.identity import SqlUserStore

DIMENSIONS = 1536
_NOW = datetime.datetime.now(datetime.timezone.utc)


def _embedding(seed: float = 0.1) -> list[float]:
    return [seed] * DIMENSIONS


def _vector_at(index: int, value: float) -> list[float]:
    vector = [0.0] * DIMENSIONS
    vector[index] = value
    return vector


def _vector_at_two(first: float, second: float) -> list[float]:
    vector = [0.0] * DIMENSIONS
    vector[0] = first
    vector[1] = second
    return vector


def _domain_record(
    *,
    owner_id: uuid.UUID,
    project_id: uuid.UUID | None = None,
    content: str = "User prefers concise answers.",
    embedding: list[float] | None = None,
) -> MemoryRecord:
    memory_type = MemoryType.PROJECT if project_id is not None else MemoryType.USER
    return MemoryRecord(
        id=uuid.uuid4(),
        memory_type=memory_type,
        scope=MemoryScope.PROJECT
        if memory_type is MemoryType.PROJECT
        else MemoryScope.USER,
        owner_id=owner_id,
        project_id=project_id,
        content=content,
        embedding=embedding if embedding is not None else _embedding(),
        created_at=_NOW,
        updated_at=_NOW,
        lifecycle_state=LifecycleState.CREATED,
        source="extraction_v1",
    )


async def _make_user(session) -> uuid.UUID:
    user = await SqlUserStore(session).create(
        sub=f"memory-{uuid.uuid4()}", email=None, name=None, picture=None
    )
    return user.id


async def _make_session(session, *, user_id: uuid.UUID) -> uuid.UUID:
    chat_session = await SqlChatStore(session).create_session(user_id=user_id)
    return chat_session.id


@pytest.fixture
def provider() -> PgVectorMemoryProvider:
    session = AsyncMock()
    settings = Settings(openai_api_key="test-key")
    return PgVectorMemoryProvider(session=session, settings=settings)


class TestPgVectorMemoryProviderScaffold:
    def test_satisfies_memory_provider_protocol(
        self, provider: PgVectorMemoryProvider
    ) -> None:
        typed: MemoryProvider = provider
        assert typed is provider

    @pytest.mark.anyio
    async def test_delete_record_not_implemented(
        self, provider: PgVectorMemoryProvider
    ) -> None:
        with pytest.raises(NotImplementedError, match="Phase 7"):
            await provider.delete_record(uuid.uuid4(), owner_id=uuid.uuid4())

    @pytest.mark.anyio
    async def test_update_lifecycle_state_not_implemented(
        self, provider: PgVectorMemoryProvider
    ) -> None:
        with pytest.raises(NotImplementedError, match="Phase 7"):
            await provider.update_lifecycle_state(
                uuid.uuid4(), owner_id=uuid.uuid4(), state=LifecycleState.ACTIVE
            )

    @pytest.mark.anyio
    async def test_get_preference_not_implemented(
        self, provider: PgVectorMemoryProvider
    ) -> None:
        with pytest.raises(NotImplementedError, match="Phase 4"):
            await provider.get_preference(user_id=uuid.uuid4(), key="response_tone")


@pytest.mark.anyio
async def test_create_and_get_record_round_trip(db_session) -> None:
    owner_id = await _make_user(db_session)
    provider = PgVectorMemoryProvider(db_session, Settings(openai_api_key="test-key"))
    record = _domain_record(owner_id=owner_id)

    created = await provider.create_record(record)
    fetched = await provider.get_record(created.id, owner_id=owner_id)

    assert fetched is not None
    assert fetched.content == record.content
    assert fetched.embedding is not None
    assert fetched.lifecycle_state is LifecycleState.CREATED


@pytest.mark.anyio
async def test_get_record_enforces_owner_isolation(db_session) -> None:
    owner_id = await _make_user(db_session)
    other_owner = await _make_user(db_session)
    provider = PgVectorMemoryProvider(db_session, Settings(openai_api_key="test-key"))
    created = await provider.create_record(_domain_record(owner_id=owner_id))

    assert await provider.get_record(created.id, owner_id=other_owner) is None


@pytest.mark.anyio
async def test_update_record_persists_changes(db_session) -> None:
    owner_id = await _make_user(db_session)
    provider = PgVectorMemoryProvider(db_session, Settings(openai_api_key="test-key"))
    created = await provider.create_record(_domain_record(owner_id=owner_id))
    updated = created.model_copy(update={"content": "Updated durable fact."})

    persisted = await provider.update_record(updated)
    fetched = await provider.get_record(created.id, owner_id=owner_id)

    assert persisted.content == "Updated durable fact."
    assert fetched is not None
    assert fetched.content == "Updated durable fact."


@pytest.mark.anyio
async def test_update_record_missing_raises_not_found(db_session) -> None:
    owner_id = await _make_user(db_session)
    provider = PgVectorMemoryProvider(db_session, Settings(openai_api_key="test-key"))
    missing = _domain_record(owner_id=owner_id)

    with pytest.raises(MemoryNotFoundError):
        await provider.update_record(missing)


@pytest.mark.anyio
async def test_search_records_returns_similar_vectors(db_session) -> None:
    owner_id = await _make_user(db_session)
    provider = PgVectorMemoryProvider(db_session, Settings(openai_api_key="test-key"))
    query = _vector_at(0, 1.0)
    await provider.create_record(
        _domain_record(
            owner_id=owner_id,
            content="Alpha",
            embedding=_vector_at(1, 1.0),
        )
    )
    target = await provider.create_record(
        _domain_record(
            owner_id=owner_id,
            content="Beta",
            embedding=_vector_at_two(0.99, 0.01),
        )
    )

    results = await provider.search_records(
        query,
        owner_id=owner_id,
        top_k=1,
    )

    assert len(results) == 1
    assert results[0].id == target.id


@pytest.mark.anyio
async def test_project_record_persists_session_scope(db_session) -> None:
    owner_id = await _make_user(db_session)
    session_id = await _make_session(db_session, user_id=owner_id)
    provider = PgVectorMemoryProvider(db_session, Settings(openai_api_key="test-key"))

    created = await provider.create_record(
        _domain_record(
            owner_id=owner_id, project_id=session_id, content="Project fact."
        )
    )

    assert created.project_id == session_id
    assert created.memory_type is MemoryType.PROJECT
