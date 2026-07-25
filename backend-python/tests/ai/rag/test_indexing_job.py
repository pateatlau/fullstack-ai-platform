"""Tests for SyncIndexingRunner / IndexingJob (Phase 9)."""

from __future__ import annotations

import logging
import uuid
from typing import cast

import pytest

from app.ai.interfaces import IndexingJob
from app.ai.rag.indexing import (
    IndexingJobFailedError,
    IndexingJobNotFoundError,
    PendingIndexingWork,
    SyncIndexingRunner,
)
from app.ai.rag.schemas import IndexingJobState


async def _succeed(work: PendingIndexingWork) -> None:
    _ = work


async def _fail(work: PendingIndexingWork) -> None:
    _ = work
    raise RuntimeError("boom")


def _work(
    *,
    user_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
) -> PendingIndexingWork:
    return PendingIndexingWork(
        document_id=document_id or uuid.uuid4(),
        user_id=user_id or uuid.uuid4(),
        file_bytes=b"hello",
        filename="doc.txt",
        mime_type="text/plain",
    )


def test_sync_indexing_runner_satisfies_indexing_job_protocol() -> None:
    runner: IndexingJob = SyncIndexingRunner(processor=_succeed)
    assert runner is not None


@pytest.mark.anyio
async def test_submit_success_reports_succeeded_status() -> None:
    runner = SyncIndexingRunner(processor=_succeed)
    work = _work()
    runner.register_pending_work(work)

    job_id = await runner.submit(
        document_id=work.document_id,
        user_id=work.user_id,
    )
    status = await runner.get_status(job_id)

    assert status.job_id == job_id
    assert status.state is IndexingJobState.SUCCEEDED
    assert status.error_message is None


@pytest.mark.anyio
async def test_submit_failure_reports_failed_status_and_reraises() -> None:
    """Failure path: status is FAILED with exception type; exception propagates."""
    runner = SyncIndexingRunner(processor=_fail)
    work = _work()
    runner.register_pending_work(work)

    with pytest.raises(IndexingJobFailedError) as exc_info:
        await runner.submit(document_id=work.document_id, user_id=work.user_id)

    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert str(exc_info.value.__cause__) == "boom"
    status = await runner.get_status(exc_info.value.job_id)
    assert status.state is IndexingJobState.FAILED
    assert status.error_message == "RuntimeError"


@pytest.mark.anyio
async def test_get_status_unknown_job_raises() -> None:
    runner = SyncIndexingRunner(processor=_succeed)
    with pytest.raises(IndexingJobNotFoundError) as exc_info:
        await runner.get_status("missing-job")
    assert exc_info.value.job_id == "missing-job"


@pytest.mark.anyio
async def test_submit_without_pending_work_fails() -> None:
    runner = SyncIndexingRunner(processor=_succeed)
    document_id = uuid.uuid4()
    user_id = uuid.uuid4()

    with pytest.raises(
        IndexingJobFailedError, match="No pending indexing work"
    ) as exc_info:
        await runner.submit(document_id=document_id, user_id=user_id)

    status = await runner.get_status(exc_info.value.job_id)
    assert status.state is IndexingJobState.FAILED
    assert status.error_message == "pending_work_missing"


@pytest.mark.anyio
async def test_submit_rejects_user_mismatch() -> None:
    runner = SyncIndexingRunner(processor=_succeed)
    work = _work()
    runner.register_pending_work(work)

    with pytest.raises(
        IndexingJobFailedError, match="No pending indexing work"
    ) as exc_info:
        await runner.submit(
            document_id=work.document_id,
            user_id=uuid.uuid4(),
        )

    status = await runner.get_status(exc_info.value.job_id)
    assert status.state is IndexingJobState.FAILED
    # Mismatched submit must not consume pending work for the owner.
    assert work.document_id in runner._pending
    job_id = await runner.submit(
        document_id=work.document_id,
        user_id=work.user_id,
    )
    assert (await runner.get_status(job_id)).state is IndexingJobState.SUCCEEDED


@pytest.mark.anyio
async def test_pending_work_consumed_after_submit() -> None:
    calls = 0

    async def _count(work: PendingIndexingWork) -> None:
        nonlocal calls
        _ = work
        calls += 1

    runner = SyncIndexingRunner(processor=_count)
    work = _work()
    runner.register_pending_work(work)
    await runner.submit(document_id=work.document_id, user_id=work.user_id)
    assert calls == 1

    with pytest.raises(IndexingJobFailedError):
        await runner.submit(document_id=work.document_id, user_id=work.user_id)


@pytest.mark.anyio
async def test_logs_job_id_and_status_without_raw_bytes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    runner = SyncIndexingRunner(processor=_succeed)
    work = _work()
    runner.register_pending_work(work)

    with caplog.at_level(logging.INFO):
        job_id = await runner.submit(
            document_id=work.document_id,
            user_id=work.user_id,
        )

    joined = " ".join(record.getMessage() for record in caplog.records)
    assert "hello" not in joined
    assert all(
        getattr(record, "file_bytes", None) != b"hello" for record in caplog.records
    )
    assert job_id
    # Structured extras are on the LogRecord, not always in getMessage().
    assert any(
        getattr(record, "indexing_job_id", None) == job_id
        and getattr(record, "indexing_job_status", None)
        == IndexingJobState.SUCCEEDED.value
        for record in caplog.records
    )


@pytest.mark.anyio
async def test_protocol_cast_round_trip() -> None:
    runner = SyncIndexingRunner(processor=_succeed)
    indexing: IndexingJob = cast(IndexingJob, runner)
    work = _work()
    # Protocol has no register_pending_work — use concrete runner for staging.
    runner.register_pending_work(work)
    job_id = await indexing.submit(
        document_id=work.document_id,
        user_id=work.user_id,
    )
    status = await indexing.get_status(job_id)
    assert status.state is IndexingJobState.SUCCEEDED
