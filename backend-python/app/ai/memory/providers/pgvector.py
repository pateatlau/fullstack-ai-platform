"""pgvector-backed ``MemoryProvider`` (scaffold — Phase 1).

Wires dependency injection so ``MemoryManager`` has a concrete provider from
Phase 1 onward. Method bodies are placeholders: durable-memory persistence
lands in Phase 3, preference persistence in Phase 4, semantic retrieval in
Phase 6, and lifecycle updates in Phase 7.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.memory.lifecycle import LifecycleState
from app.ai.memory.models import MemoryRecord, MemoryType
from app.core.config import Settings

_NOT_IMPLEMENTED = "PgVectorMemoryProvider.{method}() is implemented in {phase}."


class PgVectorMemoryProvider:
    """Concrete ``MemoryProvider`` backed by PostgreSQL + pgvector (Part I)."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def create_record(self, record: MemoryRecord) -> MemoryRecord:
        raise NotImplementedError(
            _NOT_IMPLEMENTED.format(method="create_record", phase="Phase 3")
        )

    async def update_record(self, record: MemoryRecord) -> MemoryRecord:
        raise NotImplementedError(
            _NOT_IMPLEMENTED.format(method="update_record", phase="Phase 3")
        )

    async def delete_record(self, record_id: uuid.UUID, *, owner_id: uuid.UUID) -> None:
        raise NotImplementedError(
            _NOT_IMPLEMENTED.format(method="delete_record", phase="Phase 7")
        )

    async def get_record(
        self, record_id: uuid.UUID, *, owner_id: uuid.UUID
    ) -> MemoryRecord | None:
        raise NotImplementedError(
            _NOT_IMPLEMENTED.format(method="get_record", phase="Phase 3")
        )

    async def search_records(
        self,
        query_embedding: list[float],
        *,
        owner_id: uuid.UUID,
        memory_type: MemoryType | None = None,
        session_id: uuid.UUID | None = None,
        top_k: int,
    ) -> list[MemoryRecord]:
        raise NotImplementedError(
            _NOT_IMPLEMENTED.format(method="search_records", phase="Phase 6")
        )

    async def update_lifecycle_state(
        self,
        record_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
        state: LifecycleState,
    ) -> MemoryRecord:
        raise NotImplementedError(
            _NOT_IMPLEMENTED.format(method="update_lifecycle_state", phase="Phase 7")
        )

    async def get_preference(
        self, *, user_id: uuid.UUID, key: str
    ) -> dict[str, object] | None:
        raise NotImplementedError(
            _NOT_IMPLEMENTED.format(method="get_preference", phase="Phase 4")
        )

    async def list_preferences(
        self, *, user_id: uuid.UUID
    ) -> dict[str, dict[str, object]]:
        raise NotImplementedError(
            _NOT_IMPLEMENTED.format(method="list_preferences", phase="Phase 4")
        )

    async def set_preference(
        self, *, user_id: uuid.UUID, key: str, value: dict[str, object]
    ) -> None:
        raise NotImplementedError(
            _NOT_IMPLEMENTED.format(method="set_preference", phase="Phase 4")
        )

    async def delete_preference(self, *, user_id: uuid.UUID, key: str) -> None:
        raise NotImplementedError(
            _NOT_IMPLEMENTED.format(method="delete_preference", phase="Phase 4")
        )
