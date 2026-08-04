"""Process-lifetime registry for fire-and-forget workflow run execution tasks.

Mirrors ``app/ai/memory/background_tasks.py`` — retains a strong reference to
each scheduled ``asyncio.Task`` until it completes so it is never garbage
collected mid-flight (Part I § Async run launch).
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

_RUN_TASKS: set[asyncio.Task[Any]] = set()


def schedule_run_task(coro: Coroutine[Any, Any, None]) -> asyncio.Task[Any]:
    """Schedule a background workflow run execution task and retain it."""
    task = asyncio.create_task(coro)
    _RUN_TASKS.add(task)
    task.add_done_callback(_RUN_TASKS.discard)
    return task
