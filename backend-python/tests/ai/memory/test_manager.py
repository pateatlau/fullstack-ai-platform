"""Tests for MemoryManager pass-through orchestration (Phase 1 scaffold)."""

from __future__ import annotations

import datetime
import uuid

import pytest

from app.ai.memory.manager import MemoryManager
from app.ai.memory.models import MemoryRecord, MemoryScope, MemoryType

_NOW = datetime.datetime.now(datetime.timezone.utc)


class FakeProvider:
    """Minimal fake ``MemoryProvider`` recording calls for assertion."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.record: MemoryRecord | None = None
        self.preferences: dict[tuple[uuid.UUID, str], dict[str, object]] = {}

    async def get_record(
        self, record_id: uuid.UUID, *, owner_id: uuid.UUID
    ) -> MemoryRecord | None:
        self.calls.append(
            ("get_record", {"record_id": record_id, "owner_id": owner_id})
        )
        return self.record

    async def delete_record(self, record_id: uuid.UUID, *, owner_id: uuid.UUID) -> None:
        self.calls.append(
            ("delete_record", {"record_id": record_id, "owner_id": owner_id})
        )

    async def get_preference(
        self, *, user_id: uuid.UUID, key: str
    ) -> dict[str, object] | None:
        self.calls.append(("get_preference", {"user_id": user_id, "key": key}))
        return self.preferences.get((user_id, key))

    async def set_preference(
        self, *, user_id: uuid.UUID, key: str, value: dict[str, object]
    ) -> None:
        self.calls.append(
            ("set_preference", {"user_id": user_id, "key": key, "value": value})
        )
        self.preferences[(user_id, key)] = value

    async def delete_preference(self, *, user_id: uuid.UUID, key: str) -> None:
        self.calls.append(("delete_preference", {"user_id": user_id, "key": key}))
        self.preferences.pop((user_id, key), None)


def _record(owner_id: uuid.UUID) -> MemoryRecord:
    return MemoryRecord(
        id=uuid.uuid4(),
        memory_type=MemoryType.USER,
        scope=MemoryScope.USER,
        owner_id=owner_id,
        content="Remember this.",
        created_at=_NOW,
        updated_at=_NOW,
        source="api",
    )


class TestMemoryManager:
    @pytest.mark.anyio
    async def test_get_record_delegates_to_provider(self) -> None:
        provider = FakeProvider()
        owner_id = uuid.uuid4()
        provider.record = _record(owner_id)
        manager = MemoryManager(provider=provider)  # type: ignore[arg-type]

        result = await manager.get_record(provider.record.id, owner_id=owner_id)

        assert result == provider.record
        assert provider.calls == [
            ("get_record", {"record_id": provider.record.id, "owner_id": owner_id})
        ]

    @pytest.mark.anyio
    async def test_delete_record_delegates_to_provider(self) -> None:
        provider = FakeProvider()
        owner_id = uuid.uuid4()
        record_id = uuid.uuid4()
        manager = MemoryManager(provider=provider)  # type: ignore[arg-type]

        await manager.delete_record(record_id, owner_id=owner_id)

        assert provider.calls == [
            ("delete_record", {"record_id": record_id, "owner_id": owner_id})
        ]

    @pytest.mark.anyio
    async def test_preference_round_trip_delegates_to_provider(self) -> None:
        provider = FakeProvider()
        user_id = uuid.uuid4()
        manager = MemoryManager(provider=provider)  # type: ignore[arg-type]

        assert (
            await manager.get_preference(user_id=user_id, key="response_tone") is None
        )

        await manager.set_preference(
            user_id=user_id, key="response_tone", value={"tone": "concise"}
        )
        assert await manager.get_preference(user_id=user_id, key="response_tone") == {
            "tone": "concise"
        }

        await manager.delete_preference(user_id=user_id, key="response_tone")
        assert (
            await manager.get_preference(user_id=user_id, key="response_tone") is None
        )
