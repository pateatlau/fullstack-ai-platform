"""Process-lifetime registry for fire-and-forget memory extraction tasks."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

_EXTRACTION_TASKS: set[asyncio.Task[Any]] = set()


def schedule_extraction_task(coro: Coroutine[Any, Any, None]) -> asyncio.Task[Any]:
    """Schedule a background extraction task and retain it until completion."""
    task = asyncio.create_task(coro)
    _EXTRACTION_TASKS.add(task)
    task.add_done_callback(_EXTRACTION_TASKS.discard)
    return task


def schedule_lifecycle_task(coro: Coroutine[Any, Any, None]) -> asyncio.Task[Any]:
    """Schedule lifecycle processing without blocking chat callers."""
    return schedule_extraction_task(coro)
