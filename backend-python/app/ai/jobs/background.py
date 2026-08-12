"""Process-lifetime registry for Background Jobs worker and scheduler tasks."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from dataclasses import dataclass
from typing import Any

from app.ai.jobs.handlers import register_all_handlers
from app.ai.jobs.queue import PostgresJobQueue
from app.ai.jobs.registry import JobHandlerRegistry
from app.ai.jobs.schedule_store import PostgresJobScheduleStore
from app.ai.jobs.scheduler import JobScheduler
from app.ai.jobs.worker import JobWorker
from app.core.config import Settings
from app.core.logging import get_logger
from app.db.engine import get_sessionmaker

_logger = get_logger(__name__)

_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


@dataclass(frozen=True)
class BackgroundJobsRuntime:
    """Retained worker/scheduler instances and their asyncio tasks."""

    worker: JobWorker
    scheduler: JobScheduler
    tasks: tuple[asyncio.Task[Any], ...]


def schedule_background_jobs_task(
    coro: Coroutine[Any, Any, None],
) -> asyncio.Task[Any]:
    """Schedule a background jobs loop and retain it until completion."""
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task


async def start_background_jobs(settings: Settings) -> BackgroundJobsRuntime | None:
    """Start JobWorker and JobScheduler when the feature flag is enabled."""
    if not settings.background_jobs_enabled:
        return None

    session_factory = get_sessionmaker()
    queue = PostgresJobQueue(session_factory, settings)
    store = PostgresJobScheduleStore(session_factory)
    registry = JobHandlerRegistry()
    register_all_handlers(registry)

    worker = JobWorker(queue=queue, registry=registry, settings=settings)
    scheduler = JobScheduler(queue=queue, store=store, settings=settings)

    worker_task = schedule_background_jobs_task(worker.run_forever())
    scheduler_task = schedule_background_jobs_task(scheduler.run_forever())

    _logger.info("Background Jobs worker and scheduler started")
    return BackgroundJobsRuntime(
        worker=worker,
        scheduler=scheduler,
        tasks=(worker_task, scheduler_task),
    )


async def stop_background_jobs(runtime: BackgroundJobsRuntime | None) -> None:
    """Gracefully stop worker and scheduler loops."""
    if runtime is None:
        return

    runtime.worker.request_shutdown()
    runtime.scheduler.request_shutdown()
    results = await asyncio.gather(*runtime.tasks, return_exceptions=True)
    for result in results:
        if isinstance(result, BaseException):
            _logger.warning(
                "Background Jobs task shutdown error",
                error=str(result),
                exc_info=(type(result), result, result.__traceback__),
            )
    _logger.info("Background Jobs worker and scheduler stopped")
