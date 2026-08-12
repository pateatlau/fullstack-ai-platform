"""Recurring job scheduler (Epic 10 Phase 2)."""

from __future__ import annotations

import asyncio
import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.jobs.exceptions import JobConcurrencyError
from app.ai.jobs.models import JobSchedule
from app.ai.jobs.queue import JobQueue, PostgresJobQueue
from app.ai.jobs.schedule_store import JobScheduleStore, PostgresJobScheduleStore
from app.ai.observability.metrics.instruments import record_job_enqueued
from app.core.config import Settings
from app.core.logging import get_logger

_logger = get_logger(__name__)


def compute_advanced_next_run_at(
    *,
    current_next_run_at: datetime.datetime,
    interval_seconds: int,
    now: datetime.datetime,
) -> datetime.datetime:
    """Advance past missed ticks to the next future-aligned boundary."""
    elapsed_seconds = (now - current_next_run_at).total_seconds()
    intervals_to_skip = max(1, int(elapsed_seconds // interval_seconds) + 1)
    return current_next_run_at + datetime.timedelta(
        seconds=interval_seconds * intervals_to_skip
    )


class JobScheduler:
    """Evaluates due schedules and enqueues idempotent recurring jobs."""

    def __init__(
        self,
        *,
        queue: JobQueue,
        store: JobScheduleStore,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._queue = queue
        self._store = store
        self._settings = settings
        self._session_factory = session_factory
        if session_factory is None and isinstance(queue, PostgresJobQueue):
            self._session_factory = queue.session_factory
        if session_factory is None and isinstance(store, PostgresJobScheduleStore):
            self._session_factory = store.session_factory
        self._shutdown = asyncio.Event()
        self._in_flight: set[asyncio.Task[None]] = set()

    def request_shutdown(self) -> None:
        """Stop evaluating new ticks; in-flight ticks may finish."""
        self._shutdown.set()

    async def run_forever(self) -> None:
        """Poll until :meth:`request_shutdown` and all in-flight ticks finish."""
        poll_interval = self._settings.background_jobs_scheduler_poll_interval_seconds
        while not self._shutdown.is_set():
            await self.tick_once()
            try:
                await asyncio.wait_for(self._shutdown.wait(), timeout=poll_interval)
            except TimeoutError:
                continue

        if self._in_flight:
            await asyncio.gather(*self._in_flight, return_exceptions=True)

    async def tick_once(self) -> None:
        """Evaluate all due schedules once."""
        if self._shutdown.is_set():
            return

        now = datetime.datetime.now(datetime.UTC)
        due = await self._store.list_due(now=now)
        if not due:
            return

        tasks = [
            asyncio.create_task(self._process_schedule(schedule, now))
            for schedule in due
        ]
        for task in tasks:
            self._in_flight.add(task)
            task.add_done_callback(self._in_flight.discard)
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                _logger.error(
                    "Schedule tick failed unexpectedly",
                    exc_info=(type(result), result, result.__traceback__),
                )

    async def _process_schedule(
        self,
        schedule: JobSchedule,
        now: datetime.datetime,
    ) -> None:
        tick_at = schedule.next_run_at
        idempotency_key = f"{schedule.name}:{tick_at.isoformat()}"
        next_run_at = compute_advanced_next_run_at(
            current_next_run_at=tick_at,
            interval_seconds=schedule.interval_seconds,
            now=now,
        )
        payload = dict(schedule.payload)
        if "version" not in payload:
            payload["version"] = 1

        if self._session_factory is None:
            raise RuntimeError(
                "JobScheduler requires a session factory for atomic tick processing."
            )

        if not isinstance(self._queue, PostgresJobQueue):
            raise RuntimeError(
                "Atomic schedule ticks require PostgresJobQueue in Phase 2."
            )
        if not isinstance(self._store, PostgresJobScheduleStore):
            raise RuntimeError(
                "Atomic schedule ticks require PostgresJobScheduleStore in Phase 2."
            )

        try:
            newly_enqueued = False
            async with self._session_factory() as session:
                async with session.begin():
                    # Enqueue before advance so a crash after insert but before
                    # advance can retry idempotently; roll back both when the
                    # schedule row changed underneath us (stale version).
                    _job, newly_enqueued = await self._queue.enqueue_in_transaction(
                        session,
                        job_type=schedule.job_type,
                        payload=payload,
                        run_at=now,
                        idempotency_key=idempotency_key,
                        schedule_id=schedule.id,
                    )
                    advanced = await self._store.advance_in_transaction(
                        session,
                        schedule.id,
                        expected_version=schedule.version,
                        next_run_at=next_run_at,
                    )
                    if advanced is None:
                        raise JobConcurrencyError(
                            "Concurrent schedule update lost for "
                            f"{schedule.name} (expected version "
                            f"{schedule.version})."
                        )
            if newly_enqueued:
                record_job_enqueued(job_type=schedule.job_type)
                await self._queue.reconcile_depth_metrics()
        except JobConcurrencyError:
            _logger.debug(
                "Schedule tick lost concurrent race; enqueue rolled back",
                schedule_name=schedule.name,
                schedule_id=str(schedule.id),
            )
