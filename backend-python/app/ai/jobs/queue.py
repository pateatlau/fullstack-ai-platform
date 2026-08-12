"""Postgres-backed job queue (stable public API after Phase 1)."""

from __future__ import annotations

import datetime
import os
import socket
import uuid
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.jobs.exceptions import JobConcurrencyError
from app.ai.jobs.models import BackgroundJob, JobResult, JobStatus
from app.core.config import Settings

_IDEMPOTENCY_KEY_UNIQUE_INDEX = "uq_background_jobs_idempotency_key"


def generate_worker_id() -> str:
    """Return ``{hostname}:{pid}:{uuid4}`` for ``locked_by``."""
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4()}"


def _truncate_error(error: str, *, max_len: int = 2000) -> str:
    if len(error) <= max_len:
        return error
    return error[: max_len - 3] + "..."


def _is_idempotency_key_violation(exc: IntegrityError) -> bool:
    def _matches(orig: object | None) -> bool:
        if orig is None:
            return False
        constraint_name = getattr(orig, "constraint_name", None)
        return constraint_name == _IDEMPOTENCY_KEY_UNIQUE_INDEX

    if _matches(exc.orig):
        return True

    inner = getattr(exc.orig, "__cause__", None)
    if _matches(inner):
        return True

    # SQLAlchemy's asyncpg adapter may omit constraint_name on the wrapper.
    message = str(exc.orig or exc)
    return f'"{_IDEMPOTENCY_KEY_UNIQUE_INDEX}"' in message


def _row_to_job(row: object) -> BackgroundJob:
    mapping = row._mapping  # type: ignore[attr-defined]
    return BackgroundJob(
        id=mapping["id"],
        job_type=mapping["job_type"],
        status=JobStatus(mapping["status"]),
        payload=dict(mapping["payload"] or {}),
        result=dict(mapping["result"]) if mapping["result"] is not None else None,
        attempt_count=mapping["attempt_count"],
        max_attempts=mapping["max_attempts"],
        version=mapping["version"],
        run_at=mapping["run_at"],
        locked_by=mapping["locked_by"],
        locked_at=mapping["locked_at"],
        last_error=mapping["last_error"],
        idempotency_key=mapping["idempotency_key"],
        schedule_id=mapping["schedule_id"],
        created_at=mapping["created_at"],
        updated_at=mapping["updated_at"],
        started_at=mapping["started_at"],
        finished_at=mapping["finished_at"],
    )


class JobQueue(Protocol):
    """Durable enqueue/claim/complete/fail/cancel/get/list contract."""

    async def enqueue(
        self,
        *,
        job_type: str,
        payload: dict[str, object],
        run_at: datetime.datetime | None = None,
        max_attempts: int | None = None,
        idempotency_key: str | None = None,
        schedule_id: uuid.UUID | None = None,
    ) -> BackgroundJob: ...

    async def claim_due(
        self,
        *,
        worker_id: str,
        batch_size: int,
        lease_seconds: int,
    ) -> list[BackgroundJob]: ...

    async def complete(
        self,
        job_id: uuid.UUID,
        *,
        result: JobResult,
        expected_version: int,
    ) -> BackgroundJob: ...

    async def fail(
        self,
        job_id: uuid.UUID,
        *,
        error: str,
        expected_version: int,
        retry_at: datetime.datetime | None = None,
        dead_letter: bool = False,
    ) -> BackgroundJob: ...

    async def cancel(
        self,
        job_id: uuid.UUID,
        *,
        expected_version: int,
    ) -> BackgroundJob | None: ...

    async def get(self, job_id: uuid.UUID) -> BackgroundJob | None: ...

    async def list(
        self,
        *,
        status: JobStatus | None = None,
        job_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[BackgroundJob]: ...


class PostgresJobQueue:
    """Postgres implementation using claim-and-lease row locking."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        return self._session_factory

    async def enqueue(
        self,
        *,
        job_type: str,
        payload: dict[str, object],
        run_at: datetime.datetime | None = None,
        max_attempts: int | None = None,
        idempotency_key: str | None = None,
        schedule_id: uuid.UUID | None = None,
    ) -> BackgroundJob:
        effective_run_at = run_at or datetime.datetime.now(datetime.UTC)
        effective_max_attempts = (
            max_attempts
            if max_attempts is not None
            else self._settings.background_jobs_default_max_attempts
        )
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    return await self.enqueue_in_transaction(
                        session,
                        job_type=job_type,
                        payload=payload,
                        run_at=effective_run_at,
                        max_attempts=effective_max_attempts,
                        idempotency_key=idempotency_key,
                        schedule_id=schedule_id,
                    )
        except IntegrityError as exc:
            if idempotency_key is None or not _is_idempotency_key_violation(exc):
                raise
            async with self._session_factory() as session:
                existing = await self._fetch_by_idempotency_key(
                    session, idempotency_key
                )
            if existing is None:
                raise
            return existing

    async def enqueue_in_transaction(
        self,
        session: AsyncSession,
        *,
        job_type: str,
        payload: dict[str, object],
        run_at: datetime.datetime,
        max_attempts: int | None = None,
        idempotency_key: str | None = None,
        schedule_id: uuid.UUID | None = None,
    ) -> BackgroundJob:
        """Insert a queued job within an existing transaction."""
        effective_max_attempts = (
            max_attempts
            if max_attempts is not None
            else self._settings.background_jobs_default_max_attempts
        )
        job_id = uuid.uuid4()
        insert_params = {
            "id": job_id,
            "job_type": job_type,
            "payload": _json_payload(payload),
            "max_attempts": effective_max_attempts,
            "run_at": run_at,
            "idempotency_key": idempotency_key,
            "schedule_id": schedule_id,
        }
        try:
            async with session.begin_nested():
                result = await session.execute(
                    text(
                        """
                        INSERT INTO background_jobs (
                            id, job_type, status, payload, attempt_count,
                            max_attempts, version, run_at, idempotency_key,
                            schedule_id, created_at, updated_at
                        ) VALUES (
                            :id, :job_type, 'queued', CAST(:payload AS jsonb),
                            0, :max_attempts, 1, :run_at, :idempotency_key,
                            :schedule_id, now(), now()
                        )
                        RETURNING *
                        """
                    ),
                    insert_params,
                )
                row = result.one()
        except IntegrityError as exc:
            if idempotency_key is None or not _is_idempotency_key_violation(exc):
                raise
            existing = await self._fetch_by_idempotency_key(session, idempotency_key)
            if existing is None:
                raise
            return existing

        return _row_to_job(row)

    async def claim_due(
        self,
        *,
        worker_id: str,
        batch_size: int,
        lease_seconds: int,
    ) -> list[BackgroundJob]:
        async with self._session_factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        """
                        UPDATE background_jobs
                        SET status = 'dead_letter',
                            last_error = COALESCE(
                                last_error,
                                'Lease expired with no remaining attempts'
                            ),
                            finished_at = now(),
                            locked_by = NULL,
                            locked_at = NULL,
                            updated_at = now(),
                            version = version + 1
                        WHERE status = 'running'
                          AND locked_at < now() - make_interval(secs => :lease_seconds)
                          AND attempt_count >= max_attempts
                        """
                    ),
                    {"lease_seconds": lease_seconds},
                )
                result = await session.execute(
                    text(
                        """
                        UPDATE background_jobs
                        SET status = 'running',
                            locked_by = :worker_id,
                            locked_at = now(),
                            attempt_count = attempt_count + 1,
                            started_at = COALESCE(started_at, now()),
                            updated_at = now(),
                            version = version + 1
                        WHERE id IN (
                            SELECT id FROM background_jobs
                            WHERE (
                                status = 'queued' AND run_at <= now()
                            ) OR (
                                status = 'running'
                                AND locked_at < now() - make_interval(secs => :lease_seconds)
                                AND attempt_count < max_attempts
                            )
                            ORDER BY run_at
                            LIMIT :batch_size
                            FOR UPDATE SKIP LOCKED
                        )
                        RETURNING *
                        """
                    ),
                    {
                        "worker_id": worker_id,
                        "lease_seconds": lease_seconds,
                        "batch_size": batch_size,
                    },
                )
                rows = result.fetchall()

        return [_row_to_job(row) for row in rows]

    async def complete(
        self,
        job_id: uuid.UUID,
        *,
        result: JobResult,
        expected_version: int,
    ) -> BackgroundJob:
        async with self._session_factory() as session:
            async with session.begin():
                row = await self._update_with_version(
                    session,
                    job_id=job_id,
                    expected_version=expected_version,
                    set_clause="""
                        status = 'succeeded',
                        result = CAST(:result AS jsonb),
                        finished_at = now(),
                        locked_by = NULL,
                        locked_at = NULL,
                        updated_at = now()
                    """,
                    params={"result": _json_payload(result.model_dump())},
                )
        return _row_to_job(row)

    async def fail(
        self,
        job_id: uuid.UUID,
        *,
        error: str,
        expected_version: int,
        retry_at: datetime.datetime | None = None,
        dead_letter: bool = False,
    ) -> BackgroundJob:
        truncated = _truncate_error(error)
        if dead_letter:
            set_clause = """
                status = 'dead_letter',
                last_error = :last_error,
                finished_at = now(),
                locked_by = NULL,
                locked_at = NULL,
                updated_at = now()
            """
            params: dict[str, object] = {"last_error": truncated}
        else:
            if retry_at is None:
                raise ValueError("retry_at is required when dead_letter is false.")
            set_clause = """
                status = 'queued',
                last_error = :last_error,
                run_at = :retry_at,
                locked_by = NULL,
                locked_at = NULL,
                updated_at = now()
            """
            params = {"last_error": truncated, "retry_at": retry_at}

        async with self._session_factory() as session:
            async with session.begin():
                row = await self._update_with_version(
                    session,
                    job_id=job_id,
                    expected_version=expected_version,
                    set_clause=set_clause,
                    params=params,
                )
        return _row_to_job(row)

    async def cancel(
        self,
        job_id: uuid.UUID,
        *,
        expected_version: int,
    ) -> BackgroundJob | None:
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    text(
                        """
                        UPDATE background_jobs
                        SET status = 'cancelled',
                            finished_at = now(),
                            updated_at = now(),
                            version = version + 1
                        WHERE id = :job_id
                          AND version = :expected_version
                          AND status = 'queued'
                        RETURNING *
                        """
                    ),
                    {"job_id": job_id, "expected_version": expected_version},
                )
                row = result.one_or_none()
        if row is None:
            return None
        return _row_to_job(row)

    async def get(self, job_id: uuid.UUID) -> BackgroundJob | None:
        async with self._session_factory() as session:
            result = await session.execute(
                text("SELECT * FROM background_jobs WHERE id = :job_id"),
                {"job_id": job_id},
            )
            row = result.one_or_none()
        if row is None:
            return None
        return _row_to_job(row)

    async def list(
        self,
        *,
        status: JobStatus | None = None,
        job_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[BackgroundJob]:
        clauses = ["1=1"]
        params: dict[str, object] = {"limit": limit, "offset": offset}
        if status is not None:
            clauses.append("status = :status")
            params["status"] = status.value
        if job_type is not None:
            clauses.append("job_type = :job_type")
            params["job_type"] = job_type

        query = f"""
            SELECT * FROM background_jobs
            WHERE {" AND ".join(clauses)}
            ORDER BY created_at DESC
            LIMIT :limit OFFSET :offset
        """
        async with self._session_factory() as session:
            result = await session.execute(text(query), params)
            rows = result.fetchall()
        return [_row_to_job(row) for row in rows]

    async def _update_with_version(
        self,
        session: AsyncSession,
        *,
        job_id: uuid.UUID,
        expected_version: int,
        set_clause: str,
        params: dict[str, object],
    ) -> object:
        merged = {
            "job_id": job_id,
            "expected_version": expected_version,
            **params,
        }
        result = await session.execute(
            text(
                f"""
                UPDATE background_jobs
                SET {set_clause},
                    version = version + 1
                WHERE id = :job_id
                  AND version = :expected_version
                  AND status = 'running'
                RETURNING *
                """
            ),
            merged,
        )
        row = result.one_or_none()
        if row is None:
            raise JobConcurrencyError(
                f"Concurrent update lost for job {job_id} "
                f"(expected version {expected_version})."
            )
        return row

    async def _fetch_by_idempotency_key(
        self, session: AsyncSession, idempotency_key: str
    ) -> BackgroundJob | None:
        result = await session.execute(
            text(
                "SELECT * FROM background_jobs WHERE idempotency_key = :idempotency_key"
            ),
            {"idempotency_key": idempotency_key},
        )
        row = result.one_or_none()
        if row is None:
            return None
        return _row_to_job(row)


def _json_payload(value: dict[str, object]) -> str:
    import json

    return json.dumps(value)
