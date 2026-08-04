"""Memory lifecycle domain events (Part I § Memory Event Hooks).

Event payloads never include memory content or embeddings — only identifiers
and operational metadata for future platform integrations.
"""

from __future__ import annotations

import datetime
import uuid
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, Field

from app.ai.memory.lifecycle import LifecycleState


class MemoryEventType(StrEnum):
    """Canonical memory lifecycle event types."""

    CREATED = "memory.created"


class MemoryEvent(BaseModel):
    """Domain event base for memory lifecycle notifications."""

    event_type: MemoryEventType
    record_id: uuid.UUID
    owner_id: uuid.UUID
    memory_type: str
    lifecycle_state: LifecycleState
    occurred_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )
    metadata: dict[str, object] = Field(default_factory=dict)


class MemoryEventPublisher(Protocol):
    """Publish memory lifecycle events to subscribers."""

    async def publish(self, event: MemoryEvent) -> None: ...


class LoggingMemoryEventPublisher:
    """Default publisher that records operational metadata only (no content)."""

    async def publish(self, event: MemoryEvent) -> None:
        from app.core.logging import get_logger

        logger = get_logger(__name__)
        logger.info(
            "Memory lifecycle event",
            event_type=event.event_type.value,
            record_id=str(event.record_id),
            owner_id=str(event.owner_id),
            memory_type=event.memory_type,
            lifecycle_state=event.lifecycle_state.value,
        )
