"""Audit event persistence (Part I § Audit Log Domain Model)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Protocol

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.security.audit.models import AuditEvent, AuditOutcome

_TABLE = sa.table(
    "audit_events",
    sa.column("id", sa.types.Uuid()),
    sa.column("occurred_at", sa.TIMESTAMP(timezone=True)),
    sa.column("actor_user_id", sa.types.Uuid()),
    sa.column("actor_kind", sa.Text()),
    sa.column("action", sa.Text()),
    sa.column("resource_type", sa.Text()),
    sa.column("resource_id", sa.Text()),
    sa.column("outcome", sa.Text()),
    sa.column("metadata", sa.JSON()),
    sa.column("request_id", sa.Text()),
    sa.column("trace_id", sa.Text()),
    sa.column("source_ip_hash", sa.Text()),
    sa.column("created_at", sa.TIMESTAMP(timezone=True)),
)


class AuditStore(Protocol):
    async def insert(self, event: AuditEvent) -> None: ...

    async def get_by_id(self, event_id: uuid.UUID) -> AuditEvent | None: ...

    async def count(
        self,
        *,
        actor_user_id: uuid.UUID | None = None,
        action: str | None = None,
        resource_type: str | None = None,
        outcome: AuditOutcome | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> int: ...

    async def query(
        self,
        *,
        actor_user_id: uuid.UUID | None = None,
        action: str | None = None,
        resource_type: str | None = None,
        outcome: AuditOutcome | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AuditEvent]: ...


class PostgresAuditStore:
    """Each call opens its own session — audit writes are never part of a
    caller's own transaction (Part I § Locked Decisions "Audit write
    transaction")."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def insert(self, event: AuditEvent) -> None:
        async with self._session_factory() as session:
            await session.execute(
                sa.insert(_TABLE).values(
                    id=event.id,
                    occurred_at=event.occurred_at,
                    actor_user_id=event.actor_user_id,
                    actor_kind=event.actor_kind,
                    action=event.action,
                    resource_type=event.resource_type,
                    resource_id=event.resource_id,
                    outcome=event.outcome.value,
                    metadata=event.metadata,
                    request_id=event.request_id,
                    trace_id=event.trace_id,
                    source_ip_hash=event.source_ip_hash,
                )
            )
            await session.commit()

    async def get_by_id(self, event_id: uuid.UUID) -> AuditEvent | None:
        stmt = sa.select(_TABLE).where(_TABLE.c.id == event_id)
        async with self._session_factory() as session:
            row = (await session.execute(stmt)).mappings().one_or_none()
        if row is None:
            return None
        return _row_to_event(dict(row))

    async def query(
        self,
        *,
        actor_user_id: uuid.UUID | None = None,
        action: str | None = None,
        resource_type: str | None = None,
        outcome: AuditOutcome | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[AuditEvent]:
        stmt = sa.select(_TABLE).order_by(_TABLE.c.occurred_at.desc())
        if actor_user_id is not None:
            stmt = stmt.where(_TABLE.c.actor_user_id == actor_user_id)
        if action is not None:
            stmt = stmt.where(_TABLE.c.action == action)
        if resource_type is not None:
            stmt = stmt.where(_TABLE.c.resource_type == resource_type)
        if outcome is not None:
            stmt = stmt.where(_TABLE.c.outcome == outcome.value)
        if since is not None:
            stmt = stmt.where(_TABLE.c.occurred_at >= since)
        if until is not None:
            stmt = stmt.where(_TABLE.c.occurred_at <= until)
        stmt = stmt.limit(limit).offset(offset)

        async with self._session_factory() as session:
            rows = (await session.execute(stmt)).mappings().all()
        return [_row_to_event(dict(row)) for row in rows]

    async def count(
        self,
        *,
        actor_user_id: uuid.UUID | None = None,
        action: str | None = None,
        resource_type: str | None = None,
        outcome: AuditOutcome | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> int:
        stmt = sa.select(sa.func.count()).select_from(_TABLE)
        if actor_user_id is not None:
            stmt = stmt.where(_TABLE.c.actor_user_id == actor_user_id)
        if action is not None:
            stmt = stmt.where(_TABLE.c.action == action)
        if resource_type is not None:
            stmt = stmt.where(_TABLE.c.resource_type == resource_type)
        if outcome is not None:
            stmt = stmt.where(_TABLE.c.outcome == outcome.value)
        if since is not None:
            stmt = stmt.where(_TABLE.c.occurred_at >= since)
        if until is not None:
            stmt = stmt.where(_TABLE.c.occurred_at <= until)

        async with self._session_factory() as session:
            return int((await session.execute(stmt)).scalar_one())


def _coerce_metadata(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _row_to_event(row: dict[str, object]) -> AuditEvent:
    actor_user_id = row.get("actor_user_id")
    return AuditEvent(
        id=uuid.UUID(str(row["id"])),
        occurred_at=row["occurred_at"],  # type: ignore[arg-type]
        actor_user_id=uuid.UUID(str(actor_user_id)) if actor_user_id else None,
        actor_kind=str(row["actor_kind"]),  # type: ignore[arg-type]
        action=str(row["action"]),
        resource_type=(
            str(row["resource_type"]) if row.get("resource_type") is not None else None
        ),
        resource_id=(
            str(row["resource_id"]) if row.get("resource_id") is not None else None
        ),
        outcome=AuditOutcome(row["outcome"]),
        metadata=_coerce_metadata(row.get("metadata")),
        request_id=(
            str(row["request_id"]) if row.get("request_id") is not None else None
        ),
        trace_id=str(row["trace_id"]) if row.get("trace_id") is not None else None,
        source_ip_hash=(
            str(row["source_ip_hash"])
            if row.get("source_ip_hash") is not None
            else None
        ),
        created_at=row.get("created_at"),  # type: ignore[arg-type]
    )
