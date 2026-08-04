"""Tests for LifecycleManager (Epic 05 Phase 7)."""

from __future__ import annotations

import datetime
import uuid
from typing import cast

import pytest

from app.ai.memory.events import MemoryEvent, MemoryEventType
from app.ai.memory.exceptions import MemoryError, MemoryNotFoundError
from app.ai.memory.interfaces.memory_provider import MemoryProvider
from app.ai.memory.lifecycle import LifecycleState
from app.ai.memory.lifecycle_manager import LifecycleManager
from app.ai.memory.models import MemoryRecord, MemoryScope, MemoryType
from app.core.config import Settings

_NOW = datetime.datetime.now(datetime.timezone.utc)


class RecordingPublisher:
    def __init__(self) -> None:
        self.events: list[MemoryEvent] = []

    async def publish(self, event: MemoryEvent) -> None:
        self.events.append(event)


class FakeLifecycleProvider:
    def __init__(self, record: MemoryRecord | None = None) -> None:
        self.record = record
        self.states: list[LifecycleState] = []

    async def get_record(
        self, record_id: uuid.UUID, *, owner_id: uuid.UUID
    ) -> MemoryRecord | None:
        if self.record is None or self.record.id != record_id:
            return None
        return self.record

    async def update_lifecycle_state(
        self,
        record_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
        state: LifecycleState,
        metadata: dict[str, object] | None = None,
    ) -> MemoryRecord:
        assert self.record is not None
        self.states.append(state)
        updated = self.record.model_copy(
            update={
                "lifecycle_state": state,
                "metadata": metadata or self.record.metadata,
                "updated_at": _NOW,
            }
        )
        self.record = updated
        return updated

    async def list_records(self, **kwargs):  # noqa: ANN003
        del kwargs
        return [self.record] if self.record is not None else []


def _record(*, state: LifecycleState = LifecycleState.CREATED) -> MemoryRecord:
    return MemoryRecord(
        id=uuid.uuid4(),
        memory_type=MemoryType.USER,
        scope=MemoryScope.USER,
        owner_id=uuid.uuid4(),
        content="Durable fact.",
        created_at=_NOW,
        updated_at=_NOW,
        lifecycle_state=state,
        source="test",
    )


@pytest.mark.anyio
async def test_activate_record_transitions_created_to_active() -> None:
    record = _record(state=LifecycleState.CREATED)
    provider = FakeLifecycleProvider(record)
    publisher = RecordingPublisher()
    manager = LifecycleManager(
        cast(MemoryProvider, provider),
        settings=Settings(openai_api_key="test-key"),
        event_publisher=publisher,
    )

    updated = await manager.activate_record(record)

    assert updated.lifecycle_state is LifecycleState.ACTIVE
    assert publisher.events[-1].event_type is MemoryEventType.ACTIVATED


@pytest.mark.anyio
async def test_transition_record_rejects_invalid_transition() -> None:
    record = _record(state=LifecycleState.DELETED)
    provider = FakeLifecycleProvider(record)
    manager = LifecycleManager(
        cast(MemoryProvider, provider),
        settings=Settings(openai_api_key="test-key"),
    )

    with pytest.raises(MemoryError, match="Illegal memory lifecycle transition"):
        await manager.transition_record(record, LifecycleState.ACTIVE)


@pytest.mark.anyio
async def test_delete_record_soft_deletes() -> None:
    record = _record(state=LifecycleState.ACTIVE)
    provider = FakeLifecycleProvider(record)
    manager = LifecycleManager(
        cast(MemoryProvider, provider),
        settings=Settings(openai_api_key="test-key"),
    )

    deleted = await manager.delete_record(record.id, owner_id=record.owner_id)

    assert deleted.lifecycle_state is LifecycleState.DELETED


@pytest.mark.anyio
async def test_delete_record_missing_raises_not_found() -> None:
    manager = LifecycleManager(
        cast(MemoryProvider, FakeLifecycleProvider(None)),
        settings=Settings(openai_api_key="test-key"),
    )

    with pytest.raises(MemoryNotFoundError):
        await manager.delete_record(uuid.uuid4(), owner_id=uuid.uuid4())
