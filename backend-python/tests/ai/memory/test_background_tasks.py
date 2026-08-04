"""Tests for process-lifetime memory extraction task registry."""

from __future__ import annotations

import asyncio

import pytest

from app.ai.memory.background_tasks import schedule_extraction_task


@pytest.mark.anyio
async def test_schedule_extraction_task_cleans_up_after_completion() -> None:
    import app.ai.memory.background_tasks as registry

    started = asyncio.Event()
    release = asyncio.Event()

    async def worker() -> None:
        started.set()
        await release.wait()

    schedule_extraction_task(worker())
    await started.wait()
    assert registry._EXTRACTION_TASKS  # noqa: SLF001

    release.set()
    await asyncio.sleep(0.05)
    assert not registry._EXTRACTION_TASKS  # noqa: SLF001
