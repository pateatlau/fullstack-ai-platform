"""Tests for the MemoryProvider protocol (structural conformance)."""

from __future__ import annotations

import datetime
import uuid

import pytest

from app.ai.memory.interfaces import MemoryProvider
from app.ai.memory.lifecycle import LifecycleState
from app.ai.memory.models import MemoryRecord, MemoryScope, MemoryType

_NOW = datetime.datetime.now(datetime.timezone.utc)


def _record(owner_id: uuid.UUID) -> MemoryRecord:
    return MemoryRecord(
        id=uuid.uuid4(),
        memory_type=MemoryType.USER,
        scope=MemoryScope.USER,
        owner_id=owner_id,
        content="Fake memory content.",
        created_at=_NOW,
        updated_at=_NOW,
        source="api",
    )


class FakeMemoryProvider:
    """In-memory fake used to verify Protocol conformance."""

    def __init__(self) -> None:
        self._records: dict[uuid.UUID, MemoryRecord] = {}
        self._preferences: dict[tuple[uuid.UUID, str], dict[str, object]] = {}

    async def create_record(self, record: MemoryRecord) -> MemoryRecord:
        self._records[record.id] = record
        return record

    async def update_record(self, record: MemoryRecord) -> MemoryRecord:
        self._records[record.id] = record
        return record

    async def delete_record(self, record_id: uuid.UUID, *, owner_id: uuid.UUID) -> None:
        self._records.pop(record_id, None)

    async def get_record(
        self, record_id: uuid.UUID, *, owner_id: uuid.UUID
    ) -> MemoryRecord | None:
        record = self._records.get(record_id)
        if record is None or record.owner_id != owner_id:
            return None
        return record

    async def search_records(
        self,
        query_embedding: list[float],
        *,
        owner_id: uuid.UUID,
        memory_type: MemoryType | None = None,
        session_id: uuid.UUID | None = None,
        top_k: int,
    ) -> list[MemoryRecord]:
        return [r for r in self._records.values() if r.owner_id == owner_id][:top_k]

    async def update_lifecycle_state(
        self,
        record_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
        state: LifecycleState,
        metadata: dict[str, object] | None = None,
    ) -> MemoryRecord:
        del owner_id, metadata
        record = self._records[record_id]
        updated = record.model_copy(update={"lifecycle_state": state})
        self._records[record_id] = updated
        return updated

    async def get_preference(
        self, *, user_id: uuid.UUID, key: str
    ) -> dict[str, object] | None:
        return self._preferences.get((user_id, key))

    async def list_preferences(
        self, *, user_id: uuid.UUID
    ) -> dict[str, dict[str, object]]:
        return {k: v for (uid, k), v in self._preferences.items() if uid == user_id}

    async def set_preference(
        self, *, user_id: uuid.UUID, key: str, value: dict[str, object]
    ) -> None:
        self._preferences[(user_id, key)] = value

    async def delete_preference(self, *, user_id: uuid.UUID, key: str) -> None:
        self._preferences.pop((user_id, key), None)


class TestMemoryProviderProtocol:
    @pytest.mark.anyio
    async def test_fake_provider_conforms_to_protocol(self) -> None:
        provider: MemoryProvider = FakeMemoryProvider()
        owner_id = uuid.uuid4()
        record = _record(owner_id)

        created = await provider.create_record(record)
        assert created == record

        fetched = await provider.get_record(record.id, owner_id=owner_id)
        assert fetched == record

        other_owner_fetch = await provider.get_record(record.id, owner_id=uuid.uuid4())
        assert other_owner_fetch is None

        results = await provider.search_records([0.0], owner_id=owner_id, top_k=10)
        assert results == [record]

        transitioned = await provider.update_lifecycle_state(
            record.id, owner_id=owner_id, state=LifecycleState.ACTIVE
        )
        assert transitioned.lifecycle_state is LifecycleState.ACTIVE

        await provider.delete_record(record.id, owner_id=owner_id)
        assert await provider.get_record(record.id, owner_id=owner_id) is None

    @pytest.mark.anyio
    async def test_fake_provider_preference_crud(self) -> None:
        provider: MemoryProvider = FakeMemoryProvider()
        user_id = uuid.uuid4()

        assert (
            await provider.get_preference(user_id=user_id, key="response_tone") is None
        )

        await provider.set_preference(
            user_id=user_id, key="response_tone", value={"tone": "concise"}
        )
        assert await provider.get_preference(user_id=user_id, key="response_tone") == {
            "tone": "concise"
        }
        assert await provider.list_preferences(user_id=user_id) == {
            "response_tone": {"tone": "concise"}
        }

        await provider.delete_preference(user_id=user_id, key="response_tone")
        assert (
            await provider.get_preference(user_id=user_id, key="response_tone") is None
        )
