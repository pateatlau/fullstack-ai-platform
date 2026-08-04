"""Tests for memory lifecycle events (Epic 05 Phase 3)."""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

from app.ai.memory.events import (
    LoggingMemoryEventPublisher,
    MemoryEvent,
    MemoryEventMetadata,
    MemoryEventType,
)
from app.ai.memory.lifecycle import LifecycleState


@pytest.mark.anyio
async def test_logging_publisher_emits_without_content() -> None:
    publisher = LoggingMemoryEventPublisher()
    event = MemoryEvent(
        event_type=MemoryEventType.CREATED,
        record_id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        memory_type="user",
        lifecycle_state=LifecycleState.CREATED,
        metadata=MemoryEventMetadata(source="extraction_v1"),
    )

    await publisher.publish(event)


def test_event_metadata_rejects_disallowed_fields() -> None:
    with pytest.raises(ValidationError):
        MemoryEventMetadata.model_validate(
            {"source": "extraction_v1", "content": "secret"}
        )
