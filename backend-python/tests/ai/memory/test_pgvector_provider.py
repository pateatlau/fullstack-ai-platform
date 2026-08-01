"""Tests for the PgVectorMemoryProvider scaffold (Phase 1 — no persistence yet)."""

from __future__ import annotations

import datetime
import uuid
from unittest.mock import AsyncMock

import pytest

from app.ai.memory.interfaces import MemoryProvider
from app.ai.memory.lifecycle import LifecycleState
from app.ai.memory.models import MemoryRecord, MemoryScope, MemoryType
from app.ai.memory.providers.pgvector import PgVectorMemoryProvider
from app.core.config import Settings

_NOW = datetime.datetime.now(datetime.timezone.utc)


def _record() -> MemoryRecord:
    return MemoryRecord(
        id=uuid.uuid4(),
        memory_type=MemoryType.USER,
        scope=MemoryScope.USER,
        owner_id=uuid.uuid4(),
        content="Placeholder content.",
        created_at=_NOW,
        updated_at=_NOW,
        source="api",
    )


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
    async def test_create_record_not_implemented(
        self, provider: PgVectorMemoryProvider
    ) -> None:
        with pytest.raises(NotImplementedError, match="Phase 3"):
            await provider.create_record(_record())

    @pytest.mark.anyio
    async def test_update_record_not_implemented(
        self, provider: PgVectorMemoryProvider
    ) -> None:
        with pytest.raises(NotImplementedError, match="Phase 3"):
            await provider.update_record(_record())

    @pytest.mark.anyio
    async def test_get_record_not_implemented(
        self, provider: PgVectorMemoryProvider
    ) -> None:
        with pytest.raises(NotImplementedError, match="Phase 3"):
            await provider.get_record(uuid.uuid4(), owner_id=uuid.uuid4())

    @pytest.mark.anyio
    async def test_delete_record_not_implemented(
        self, provider: PgVectorMemoryProvider
    ) -> None:
        with pytest.raises(NotImplementedError, match="Phase 7"):
            await provider.delete_record(uuid.uuid4(), owner_id=uuid.uuid4())

    @pytest.mark.anyio
    async def test_search_records_not_implemented(
        self, provider: PgVectorMemoryProvider
    ) -> None:
        with pytest.raises(NotImplementedError, match="Phase 6"):
            await provider.search_records([0.0], owner_id=uuid.uuid4(), top_k=5)

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
    async def test_list_preferences_not_implemented(
        self, provider: PgVectorMemoryProvider
    ) -> None:
        with pytest.raises(NotImplementedError, match="Phase 4"):
            await provider.list_preferences(user_id=uuid.uuid4())

    @pytest.mark.anyio
    async def test_set_preference_not_implemented(
        self, provider: PgVectorMemoryProvider
    ) -> None:
        with pytest.raises(NotImplementedError, match="Phase 4"):
            await provider.set_preference(
                user_id=uuid.uuid4(), key="response_tone", value={"tone": "concise"}
            )

    @pytest.mark.anyio
    async def test_delete_preference_not_implemented(
        self, provider: PgVectorMemoryProvider
    ) -> None:
        with pytest.raises(NotImplementedError, match="Phase 4"):
            await provider.delete_preference(user_id=uuid.uuid4(), key="response_tone")
