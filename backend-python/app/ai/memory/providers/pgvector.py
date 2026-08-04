"""pgvector-backed ``MemoryProvider`` (Epic 05).

Phase 3 implements durable record CRUD and similarity search for dedupe.
Phase 4 implements structured preference persistence. Phase 5 enforces project
memory session isolation on updates. Phase 7 implements lifecycle updates,
soft deletion, and record listing for the REST API.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.memory.exceptions import MemoryAccessDeniedError, MemoryNotFoundError
from app.ai.memory.lifecycle import LifecycleState, validate_transition
from app.ai.memory.models import MemoryRecord, MemoryScope, MemoryType
from app.core.config import Settings
from app.db.models import MemoryRecord as DbMemoryRecord
from app.db.models import UserPreference as DbUserPreference


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

        _assert_memory_type_immutable(existing, record)
        _assert_memory_type_scope(record)

        existing.session_id = record.project_id
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
        existing = await self._session.scalar(
            select(DbMemoryRecord).where(
                DbMemoryRecord.id == record_id,
                DbMemoryRecord.owner_id == owner_id,
            )
        )
        if existing is None:
            raise MemoryNotFoundError(
                f"Memory record {record_id} not found for owner {owner_id}."
            )
        if LifecycleState(existing.lifecycle_state) is LifecycleState.DELETED:
            return
        await self.update_lifecycle_state(
            record_id,
            owner_id=owner_id,
            state=LifecycleState.DELETED,
            metadata={
                "deleted_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
            },
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
                DbMemoryRecord.lifecycle_state != LifecycleState.ARCHIVED.value,
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

    async def list_records(
        self,
        *,
        owner_id: uuid.UUID,
        memory_type: MemoryType | None = None,
        session_id: uuid.UUID | None = None,
        lifecycle_state: LifecycleState | None = None,
        include_deleted: bool = False,
        limit: int = 100,
    ) -> list[MemoryRecord]:
        """List caller-owned records for management APIs and lifecycle passes."""
        stmt = (
            select(DbMemoryRecord)
            .where(DbMemoryRecord.owner_id == owner_id)
            .order_by(DbMemoryRecord.created_at.desc())
            .limit(limit)
        )
        if not include_deleted:
            stmt = stmt.where(
                DbMemoryRecord.lifecycle_state != LifecycleState.DELETED.value
            )
        if memory_type is not None:
            stmt = stmt.where(DbMemoryRecord.memory_type == memory_type.value)
        if session_id is not None:
            stmt = stmt.where(DbMemoryRecord.session_id == session_id)
        if lifecycle_state is not None:
            stmt = stmt.where(DbMemoryRecord.lifecycle_state == lifecycle_state.value)

        rows = (await self._session.execute(stmt)).scalars().all()
        return [_to_domain(row) for row in rows]

    async def update_lifecycle_state(
        self,
        record_id: uuid.UUID,
        *,
        owner_id: uuid.UUID,
        state: LifecycleState,
        metadata: dict[str, object] | None = None,
    ) -> MemoryRecord:
        existing = await self._session.scalar(
            select(DbMemoryRecord).where(
                DbMemoryRecord.id == record_id,
                DbMemoryRecord.owner_id == owner_id,
            )
        )
        if existing is None:
            raise MemoryNotFoundError(
                f"Memory record {record_id} not found for owner {owner_id}."
            )

        current = LifecycleState(existing.lifecycle_state)
        validate_transition(current, state)

        merged_metadata = dict(existing.metadata_json)
        if metadata:
            merged_metadata.update(metadata)

        now = datetime.datetime.now(datetime.timezone.utc)
        existing.lifecycle_state = state.value
        existing.metadata_json = merged_metadata
        existing.updated_at = now
        await self._session.flush()
        await self._session.refresh(existing)
        return _to_domain(existing)

    async def get_preference(
        self, *, user_id: uuid.UUID, key: str
    ) -> dict[str, object] | None:
        row = await self._session.scalar(
            select(DbUserPreference).where(
                DbUserPreference.user_id == user_id,
                DbUserPreference.key == key,
            )
        )
        if row is None:
            return None
        return dict(row.value)

    async def list_preferences(
        self, *, user_id: uuid.UUID
    ) -> dict[str, dict[str, object]]:
        rows = (
            (
                await self._session.execute(
                    select(DbUserPreference)
                    .where(DbUserPreference.user_id == user_id)
                    .order_by(DbUserPreference.key)
                )
            )
            .scalars()
            .all()
        )
        return {row.key: dict(row.value) for row in rows}

    async def set_preference(
        self, *, user_id: uuid.UUID, key: str, value: dict[str, object]
    ) -> None:
        existing = await self._session.scalar(
            select(DbUserPreference).where(
                DbUserPreference.user_id == user_id,
                DbUserPreference.key == key,
            )
        )
        if existing is None:
            self._session.add(DbUserPreference(user_id=user_id, key=key, value=value))
        else:
            existing.value = value
        await self._session.flush()

    async def delete_preference(self, *, user_id: uuid.UUID, key: str) -> None:
        row = await self._session.scalar(
            select(DbUserPreference).where(
                DbUserPreference.user_id == user_id,
                DbUserPreference.key == key,
            )
        )
        if row is not None:
            await self._session.delete(row)
            await self._session.flush()


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
    _assert_memory_type_scope(record)
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


def _assert_memory_type_scope(record: MemoryRecord) -> None:
    """Ensure memory_type and session scope stay aligned at ORM conversion."""
    if record.memory_type is MemoryType.PROJECT:
        if record.project_id is None:
            raise ValueError("project_id is required for project memory.")
        return
    if record.project_id is not None:
        raise MemoryAccessDeniedError(
            "User memory cannot be associated with a session."
        )


def _assert_memory_type_immutable(
    existing: DbMemoryRecord, record: MemoryRecord
) -> None:
    """Reject updates that change memory domain or project session ownership."""
    existing_type = MemoryType(existing.memory_type)
    if existing_type is MemoryType.PROJECT:
        if record.memory_type is not MemoryType.PROJECT:
            raise MemoryAccessDeniedError(
                "Cannot convert project memory to user scope."
            )
        if record.project_id != existing.session_id:
            raise MemoryAccessDeniedError(
                "Cannot move project memory to a different session."
            )
        return
    if record.memory_type is MemoryType.PROJECT:
        raise MemoryAccessDeniedError("Cannot convert user memory to project scope.")
