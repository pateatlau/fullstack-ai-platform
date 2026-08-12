"""Schedule persistence for recurring background jobs (Epic 10 Phase 2)."""

from __future__ import annotations

import datetime
import uuid
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.jobs.models import JobSchedule, ScheduleStatus


def _row_to_schedule(row: object) -> JobSchedule:
    mapping = row._mapping  # type: ignore[attr-defined]
    return JobSchedule(
        id=mapping["id"],
        name=mapping["name"],
        job_type=mapping["job_type"],
        payload=dict(mapping["payload"] or {}),
        interval_seconds=mapping["interval_seconds"],
        next_run_at=mapping["next_run_at"],
        version=mapping["version"],
        status=ScheduleStatus(mapping["status"]),
        created_at=mapping["created_at"],
        updated_at=mapping["updated_at"],
    )


class JobScheduleStore(Protocol):
    """Schedule CRUD persistence contract."""

    async def list_due(self, *, now: datetime.datetime) -> list[JobSchedule]: ...

    async def advance(
        self,
        schedule_id: uuid.UUID,
        *,
        expected_version: int,
        next_run_at: datetime.datetime,
    ) -> JobSchedule | None: ...

    async def list_all(self) -> list[JobSchedule]: ...

    async def get_by_name(self, name: str) -> JobSchedule | None: ...

    async def set_status(
        self,
        schedule_id: uuid.UUID,
        *,
        expected_version: int,
        status: ScheduleStatus,
    ) -> JobSchedule | None: ...


class PostgresJobScheduleStore:
    """Postgres-backed schedule store with optimistic versioning."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        return self._session_factory

    async def list_due(self, *, now: datetime.datetime) -> list[JobSchedule]:
        async with self._session_factory() as session:
            result = await session.execute(
                text(
                    """
                    SELECT *
                    FROM background_job_schedules
                    WHERE status = 'enabled'
                      AND next_run_at <= :now
                    ORDER BY next_run_at
                    """
                ),
                {"now": now},
            )
            rows = result.fetchall()
        return [_row_to_schedule(row) for row in rows]

    async def advance(
        self,
        schedule_id: uuid.UUID,
        *,
        expected_version: int,
        next_run_at: datetime.datetime,
    ) -> JobSchedule | None:
        async with self._session_factory() as session:
            async with session.begin():
                return await self.advance_in_transaction(
                    session,
                    schedule_id,
                    expected_version=expected_version,
                    next_run_at=next_run_at,
                )

    async def advance_in_transaction(
        self,
        session: AsyncSession,
        schedule_id: uuid.UUID,
        *,
        expected_version: int,
        next_run_at: datetime.datetime,
    ) -> JobSchedule | None:
        result = await session.execute(
            text(
                """
                UPDATE background_job_schedules
                SET next_run_at = :next_run_at,
                    updated_at = now(),
                    version = version + 1
                WHERE id = :schedule_id
                  AND version = :expected_version
                RETURNING *
                """
            ),
            {
                "schedule_id": schedule_id,
                "expected_version": expected_version,
                "next_run_at": next_run_at,
            },
        )
        row = result.one_or_none()
        if row is None:
            return None
        return _row_to_schedule(row)

    async def list_all(self) -> list[JobSchedule]:
        async with self._session_factory() as session:
            result = await session.execute(
                text(
                    """
                    SELECT *
                    FROM background_job_schedules
                    ORDER BY name
                    """
                )
            )
            rows = result.fetchall()
        return [_row_to_schedule(row) for row in rows]

    async def get(self, schedule_id: uuid.UUID) -> JobSchedule | None:
        """Load a schedule by id (test helper)."""
        async with self._session_factory() as session:
            result = await session.execute(
                text("SELECT * FROM background_job_schedules WHERE id = :schedule_id"),
                {"schedule_id": schedule_id},
            )
            row = result.one_or_none()
        if row is None:
            return None
        return _row_to_schedule(row)

    async def get_by_name(self, name: str) -> JobSchedule | None:
        """Load a schedule by its unique name."""
        async with self._session_factory() as session:
            result = await session.execute(
                text("SELECT * FROM background_job_schedules WHERE name = :name"),
                {"name": name},
            )
            row = result.one_or_none()
        if row is None:
            return None
        return _row_to_schedule(row)

    async def set_status(
        self,
        schedule_id: uuid.UUID,
        *,
        expected_version: int,
        status: ScheduleStatus,
    ) -> JobSchedule | None:
        """Update schedule status with optimistic versioning."""
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    text(
                        """
                        UPDATE background_job_schedules
                        SET status = :status,
                            updated_at = now(),
                            version = version + 1
                        WHERE id = :schedule_id
                          AND version = :expected_version
                        RETURNING *
                        """
                    ),
                    {
                        "schedule_id": schedule_id,
                        "expected_version": expected_version,
                        "status": status.value,
                    },
                )
                row = result.one_or_none()
        if row is None:
            return None
        return _row_to_schedule(row)

    async def insert_schedule(
        self,
        *,
        name: str,
        job_type: str,
        payload: dict[str, object],
        interval_seconds: int,
        next_run_at: datetime.datetime,
        status: ScheduleStatus = ScheduleStatus.ENABLED,
    ) -> JobSchedule:
        """Insert a schedule row (test helper)."""
        schedule_id = uuid.uuid4()
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    text(
                        """
                        INSERT INTO background_job_schedules (
                            id, name, job_type, payload, interval_seconds,
                            next_run_at, version, status, created_at, updated_at
                        ) VALUES (
                            :id, :name, :job_type, CAST(:payload AS jsonb),
                            :interval_seconds, :next_run_at, 1, :status,
                            now(), now()
                        )
                        RETURNING *
                        """
                    ),
                    {
                        "id": schedule_id,
                        "name": name,
                        "job_type": job_type,
                        "payload": _json_payload(payload),
                        "interval_seconds": interval_seconds,
                        "next_run_at": next_run_at,
                        "status": status.value,
                    },
                )
                row = result.one()
        return _row_to_schedule(row)


def _json_payload(value: dict[str, object]) -> str:
    import json

    return json.dumps(value)
