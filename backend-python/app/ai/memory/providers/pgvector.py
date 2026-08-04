"""pgvector-backed ``MemoryProvider`` (Epic 05).

Phase 3 implements durable record CRUD and similarity search for dedupe.
Preference persistence lands in Phase 4; lifecycle updates in Phase 7.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.memory.exceptions import MemoryNotFoundError
from app.ai.memory.lifecycle import LifecycleState
from app.ai.memory.models import MemoryRecord, MemoryScope, MemoryType
from app.core.config import Settings
from app.db.models import MemoryRecord as DbMemoryRecord

_NOT_IMPLEMENTED = "PgVectorMemoryProvider.{method}() is implemented in {phase}."


class PgVectorMemoryProvider:
    """Concrete ``MemoryProvider`` backed by PostgreSQL + pgvector (Part I)."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def create_record(self, record: MemoryRecord) -> MemoryRecord:
        _validate_embedding(record.embedding, self._settings.embedding_dimensions)
        row = _to_orm(record)
        self._session.add(row)
        await self._session.flush()
        return _to_domain(row)

    async def update_record(self, record: MemoryRecord) -> MemoryRecord:
        _validate_embedding(record.embedding, self._settings.embedding_dimensions)
        existing = await self._session.scalar(
            select(DbMemoryRecord).where(
                DbMemoryRecord.id == record.id,
                DbMemoryRecord.owner_id == record.owner_id,
            )
        )
        if existing is None:
            raise MemoryNotFoundError(
                f"Memory record {record.id} not found for owner {record.owner_id}."
            )

        existing.session_id = record.project_id
        existing.memory_type = record.memory_type.value
        existing.title = record.title
        existing.content = record.content
        existing.summary = record.summary
        existing.embedding = record.embedding
        existing.metadata_json = record.metadata
        existing.importance = record.importance
        existing.confidence = record.confidence
        existing.quality_score = record.quality_score
        existing.lifecycle_state = record.lifecycle_state.value
        existing.source = record.source
        existing.last_accessed_at = record.last_accessed_at
        existing.expires_at = record.expires_at
        await self._session.flush()
        await self._session.refresh(existing)
        return _to_domain(existing)

    async def delete_record(self, record_id: uuid.UUID, *, owner_id: uuid.UUID) -> None:
        raise NotImplementedError(
            _NOT_IMPLEMENTED.format(method="delete_record", phase="Phase 7")
        )

    async def get_record(
        self, record_id: uuid.UUID, *, owner_id: uuid.UUID
    ) -> MemoryRecord | None:
        row = await self._session.scalar(
            select(DbMemoryRecord).where(
                DbMemoryRecord.id == record_id,
                DbMemoryRecord.owner_id == owner_id,
            )
        )
        if row is None:
            return None
        return _to_domain(row)

    async def search_records(
        self,
        query_embedding: list[float],
        *,
        owner_id: uuid.UUID,
        memory_type: MemoryType | None = None,
        session_id: uuid.UUID | None = None,
        top_k: int,
    ) -> list[MemoryRecord]:
        if top_k < 1:
            return []

        distance = DbMemoryRecord.embedding.cosine_distance(query_embedding)
        stmt = (
            select(DbMemoryRecord)
            .where(
                DbMemoryRecord.owner_id == owner_id,
                DbMemoryRecord.embedding.is_not(None),
                DbMemoryRecord.lifecycle_state != LifecycleState.DELETED.value,
            )
            .order_by(distance)
            .limit(top_k)
        )
        if memory_type is not None:
            stmt = stmt.where(DbMemoryRecord.memory_type == memory_type.value)
        if session_id is not None:
            stmt = stmt.where(DbMemoryRecord.session_id == session_id)

        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_domain(row) for row in rows]

    async def list_active_records(
        self,
        *,
        owner_id: uuid.UUID,
        memory_type: MemoryType | None = None,
        session_id: uuid.UUID | None = None,
        limit: int = 100,
    ) -> list[MemoryRecord]:
        """Return active records for dedupe — package-internal helper (not on Protocol)."""
        stmt = (
            select(DbMemoryRecord)
            .where(
                DbMemoryRecord.owner_id == owner_id,
                DbMemoryRecord.lifecycle_state.not_in(
                    (
                        LifecycleState.DELETED.value,
                        LifecycleState.ARCHIVED.value,
                    )
                ),
            )
            .order_by(DbMemoryRecord.created_at.desc())
            .limit(limit)
        )
        if memory_type is not None:
            stmt = stmt.where(DbMemoryRecord.memory_type == memory_type.value)
        if session_id is not None:
            stmt = stmt.where(DbMemoryRecord.session_id == session_id)

        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_domain(row) for row in rows]

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


def _to_domain(row: DbMemoryRecord) -> MemoryRecord:
    memory_type = MemoryType(row.memory_type)
    return MemoryRecord(
        id=row.id,
        memory_type=memory_type,
        scope=MemoryScope.PROJECT
        if memory_type is MemoryType.PROJECT
        else MemoryScope.USER,
        owner_id=row.owner_id,
        project_id=row.session_id,
        title=row.title,
        content=row.content,
        summary=row.summary,
        embedding=list(row.embedding) if row.embedding is not None else None,
        metadata=dict(row.metadata_json),
        importance=row.importance,
        confidence=row.confidence,
        quality_score=row.quality_score,
        created_at=row.created_at,
        updated_at=row.updated_at,
        last_accessed_at=row.last_accessed_at,
        expires_at=row.expires_at,
        lifecycle_state=LifecycleState(row.lifecycle_state),
        source=row.source,
    )


def _validate_embedding(
    embedding: list[float] | None, expected_dimensions: int
) -> None:
    if embedding is None:
        return
    if len(embedding) != expected_dimensions:
        raise ValueError(
            f"Embedding dimension {len(embedding)} does not match "
            f"configured {expected_dimensions}."
        )


def _to_orm(record: MemoryRecord) -> DbMemoryRecord:
    return DbMemoryRecord(
        id=record.id,
        owner_id=record.owner_id,
        session_id=record.project_id,
        memory_type=record.memory_type.value,
        title=record.title,
        content=record.content,
        summary=record.summary,
        embedding=record.embedding,
        metadata_json=record.metadata,
        importance=record.importance,
        confidence=record.confidence,
        quality_score=record.quality_score,
        lifecycle_state=record.lifecycle_state.value,
        source=record.source,
        created_at=record.created_at,
        updated_at=record.updated_at,
        last_accessed_at=record.last_accessed_at,
        expires_at=record.expires_at,
    )
