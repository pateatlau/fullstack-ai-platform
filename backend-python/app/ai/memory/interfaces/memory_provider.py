"""MemoryProvider protocol (public API — stable after Phase 1).

All Memory storage implementations (pgvector, future Pinecone/Weaviate/Qdrant)
implement this interface. The rest of the platform depends only on this
protocol, never on a concrete provider (Part I § Memory Provider Contract).
"""

from __future__ import annotations

import uuid
from typing import Protocol

from app.ai.memory.lifecycle import LifecycleState
from app.ai.memory.models import MemoryRecord, MemoryType


class MemoryProvider(Protocol):
    """Persist and retrieve ``MemoryRecord`` and ``UserPreference`` data."""

    async def create_record(self, record: MemoryRecord) -> MemoryRecord: ...

    async def update_record(self, record: MemoryRecord) -> MemoryRecord: ...

    async def delete_record(
        self, record_id: uuid.UUID, *, owner_id: uuid.UUID
    ) -> None: ...

    async def get_record(
        self, record_id: uuid.UUID, *, owner_id: uuid.UUID
    ) -> MemoryRecord | None: ...

    async def search_records(
        self,
        query_embedding: list[float],
        *,
        owner_id: uuid.UUID,
        memory_type: MemoryType | None = None,
        session_id: uuid.UUID | None = None,
        top_k: int,
    ) -> list[MemoryRecord]: ...

    async def update_lifecycle_state(
        self,
        record_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
        state: LifecycleState,
        metadata: dict[str, object] | None = None,
    ) -> MemoryRecord: ...

    async def get_preference(
        self, *, user_id: uuid.UUID, key: str
    ) -> dict[str, object] | None: ...

    async def list_preferences(
        self, *, user_id: uuid.UUID
    ) -> dict[str, dict[str, object]]: ...

    async def set_preference(
        self, *, user_id: uuid.UUID, key: str, value: dict[str, object]
    ) -> None: ...

    async def delete_preference(self, *, user_id: uuid.UUID, key: str) -> None: ...
