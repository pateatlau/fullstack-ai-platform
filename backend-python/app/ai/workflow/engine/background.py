"""Process-lifetime registry for fire-and-forget workflow run execution tasks.

Mirrors ``app/ai/memory/background_tasks.py`` — retains a strong reference to
each scheduled ``asyncio.Task`` until it completes so it is never garbage
collected mid-flight (Part I § Async run launch).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Coroutine
from typing import TYPE_CHECKING, Any

from app.ai.workflow.models import RunStatus
from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.ai.workflow.interfaces.workflow_store import WorkflowStore

_RUN_TASKS: set[asyncio.Task[Any]] = set()
_ACTIVE_RUN_IDS: set[uuid.UUID] = set()

_logger = get_logger(__name__)


def schedule_run_task(
    coro: Coroutine[Any, Any, None],
    *,
    run_id: uuid.UUID,
) -> asyncio.Task[Any]:
    """Schedule a background workflow run execution task and retain it."""
    _ACTIVE_RUN_IDS.add(run_id)
    task = asyncio.create_task(coro)
    _RUN_TASKS.add(task)
    task.add_done_callback(_release_run_task(run_id))
    return task


def _release_run_task(run_id: uuid.UUID) -> Callable[[asyncio.Task[Any]], None]:
    """Return a done-callback that clears task retention and active-run tracking."""

    def _on_done(task: asyncio.Task[Any]) -> None:
        _RUN_TASKS.discard(task)
        _ACTIVE_RUN_IDS.discard(run_id)

    return _on_done


def is_run_active(run_id: uuid.UUID) -> bool:
    """Return True when an in-process executor is scheduled or driving ``run_id``."""
    return run_id in _ACTIVE_RUN_IDS


async def reconcile_orphaned_runs(
    store: WorkflowStore,
    *,
    schedule_run: Callable[[uuid.UUID, uuid.UUID], Awaitable[None] | None],
) -> int:
    """Reattach executors to persisted ``running`` runs after process restart."""
    orphaned = 0
    running_runs = await store.list_runs_by_status(status=RunStatus.RUNNING)
    for run in running_runs:
        if is_run_active(run.id):
            continue
        _logger.info(
            "Reconciling orphaned workflow run",
            run_id=str(run.id),
            owner_id=str(run.owner_id),
        )
        maybe = schedule_run(run.id, run.owner_id)
        if maybe is not None:
            await maybe
        orphaned += 1
    return orphaned
