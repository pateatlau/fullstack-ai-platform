"""Scheduled evaluation run handler tests (Epic 10 Phase 6)."""

from __future__ import annotations

import datetime
import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.ai.evaluation.cli import run_eval
from app.ai.evaluation.report import EvalCaseResult, EvalRunReport
from app.ai.jobs.background import start_background_jobs, stop_background_jobs
from app.ai.jobs.handlers.scheduled_eval import (
    EvaluationRunFailedError,
    SCHEDULED_EVALUATION_SCHEDULE_NAME,
    reconcile_evaluation_schedule_status,
    scheduled_evaluation_run,
)
from app.ai.jobs.models import BackgroundJob, JobSchedule, JobStatus, ScheduleStatus
from app.ai.jobs.queue import PostgresJobQueue
from app.ai.jobs.registry import JobHandlerRegistry
from app.ai.jobs.schedule_store import PostgresJobScheduleStore
from app.ai.jobs.worker import JobWorker
from app.core.config import Settings, get_settings
from tests.ai.jobs.conftest import (
    background_job_schedules_table_available,
    background_jobs_table_available,
    make_queue_session_factory,
)

DATASET = Path("tests/data/evaluation/sample.yaml")


def _args(**overrides: object):
    from argparse import Namespace

    base = {
        "level": "prompt",
        "dataset": DATASET,
        "output": Path(".eval/test-eval-report.json"),
        "use_judge": False,
        "check_regression": None,
        "regression_output": Path(".eval/test-regression-result.json"),
        "update_baseline": None,
    }
    base.update(overrides)
    return Namespace(**base)


def _sample_job(*, job_id: uuid.UUID | None = None) -> BackgroundJob:
    now = datetime.datetime.now(datetime.UTC)
    return BackgroundJob(
        id=job_id or uuid.uuid4(),
        job_type="scheduled_evaluation_run",
        status=JobStatus.RUNNING,
        payload={"version": 1},
        attempt_count=1,
        max_attempts=3,
        version=1,
        run_at=now,
        created_at=now,
        updated_at=now,
        locked_by="test-worker",
        locked_at=now,
        started_at=now,
    )


def _eval_settings(**overrides: object) -> Settings:
    base = {
        "openai_api_key": "test-key",
        "background_jobs_enabled": True,
        "evaluation_schedule_level": "prompt",
        "evaluation_schedule_enabled": False,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


async def _insert_evaluation_schedule(
    store: PostgresJobScheduleStore,
    *,
    status: ScheduleStatus,
) -> uuid.UUID:
    due_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=60)
    schedule = await store.insert_schedule(
        name=SCHEDULED_EVALUATION_SCHEDULE_NAME,
        job_type="scheduled_evaluation_run",
        payload={"version": 1},
        interval_seconds=86400,
        next_run_at=due_at,
        status=status,
    )
    return schedule.id


@pytest.mark.anyio
async def test_scheduled_eval_matches_manual_cli_at_prompt_level(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(Path(__file__).resolve().parents[4])
    get_settings.cache_clear()

    manual_output = tmp_path / "manual-report.json"
    cli_exit = await run_eval(_args(level="prompt", output=manual_output))
    assert cli_exit == 0
    manual_payload = json.loads(manual_output.read_text(encoding="utf-8"))

    job = _sample_job()
    settings = _eval_settings(evaluation_schedule_level="prompt")
    result = await scheduled_evaluation_run(
        job,
        settings=settings,
        output_dir=tmp_path,
    )

    assert result.counts["passed"] == manual_payload["summary"]["passed"]
    assert result.counts["failed"] == manual_payload["summary"]["failed"]
    assert result.counts["skipped"] == manual_payload["summary"]["skipped"]
    expected_report = tmp_path / f"scheduled-eval-{job.id}.json"
    assert result.ref_id == str(expected_report)
    assert expected_report.exists()


@pytest.mark.anyio
async def test_evaluation_schedule_stays_disabled_when_flag_off(
    db_session,
) -> None:
    if not await background_job_schedules_table_available(db_session):
        pytest.skip("background_job_schedules not available — run alembic upgrade head")

    factory = make_queue_session_factory(db_session.bind)
    store = PostgresJobScheduleStore(factory)
    schedule_id = await _insert_evaluation_schedule(
        store,
        status=ScheduleStatus.DISABLED,
    )

    await reconcile_evaluation_schedule_status(
        store,
        _eval_settings(evaluation_schedule_enabled=False),
    )

    updated = await store.get(schedule_id)
    assert updated is not None
    assert updated.status is ScheduleStatus.DISABLED


@pytest.mark.anyio
async def test_evaluation_schedule_enabled_when_flag_on(
    db_session,
) -> None:
    if not await background_job_schedules_table_available(db_session):
        pytest.skip("background_job_schedules not available — run alembic upgrade head")

    factory = make_queue_session_factory(db_session.bind)
    store = PostgresJobScheduleStore(factory)
    schedule_id = await _insert_evaluation_schedule(
        store,
        status=ScheduleStatus.DISABLED,
    )

    await reconcile_evaluation_schedule_status(
        store,
        _eval_settings(evaluation_schedule_enabled=True),
    )

    updated = await store.get(schedule_id)
    assert updated is not None
    assert updated.status is ScheduleStatus.ENABLED


@pytest.mark.anyio
async def test_reconcile_retries_on_optimistic_version_mismatch() -> None:
    now = datetime.datetime.now(datetime.UTC)
    schedule_id = uuid.uuid4()
    stale = JobSchedule(
        id=schedule_id,
        name=SCHEDULED_EVALUATION_SCHEDULE_NAME,
        job_type="scheduled_evaluation_run",
        payload={"version": 1},
        interval_seconds=86400,
        next_run_at=now,
        version=1,
        status=ScheduleStatus.DISABLED,
        created_at=now,
        updated_at=now,
    )
    current = stale.model_copy(update={"version": 2})
    enabled = current.model_copy(
        update={"status": ScheduleStatus.ENABLED, "version": 3}
    )

    store = AsyncMock(spec=PostgresJobScheduleStore)
    store.get_by_name = AsyncMock(side_effect=[stale, current])
    store.set_status = AsyncMock(side_effect=[None, enabled])

    await reconcile_evaluation_schedule_status(
        store,
        _eval_settings(evaluation_schedule_enabled=True),
    )

    assert store.get_by_name.call_count == 2
    assert store.set_status.call_count == 2
    store.set_status.assert_any_call(
        schedule_id,
        expected_version=1,
        status=ScheduleStatus.ENABLED,
    )
    store.set_status.assert_any_call(
        schedule_id,
        expected_version=2,
        status=ScheduleStatus.ENABLED,
    )


@pytest.mark.anyio
async def test_disabled_evaluation_schedule_is_not_due(
    db_session,
) -> None:
    if not await background_job_schedules_table_available(db_session):
        pytest.skip("background_job_schedules not available — run alembic upgrade head")

    factory = make_queue_session_factory(db_session.bind)
    store = PostgresJobScheduleStore(factory)
    await _insert_evaluation_schedule(store, status=ScheduleStatus.DISABLED)

    due = await store.list_due(now=datetime.datetime.now(datetime.UTC))
    assert not any(
        schedule.name == SCHEDULED_EVALUATION_SCHEDULE_NAME for schedule in due
    )


@pytest.mark.anyio
async def test_failing_eval_surfaces_handler_failure_with_last_error(
    db_session,
    tmp_path: Path,
) -> None:
    if not await background_jobs_table_available(db_session):
        pytest.skip("background_jobs not available — run alembic upgrade head")

    failing_report = EvalRunReport(
        dataset_path=str(DATASET),
        settings_snapshot={},
        results=[
            EvalCaseResult(
                case_id="prompt_smoke_1",
                level="prompt",
                passed=False,
                latency_ms=1,
                error="forced failure",
            )
        ],
    )

    async def fake_run_with_session(
        **kwargs: object,
    ) -> tuple[EvalRunReport, int | None]:
        del kwargs
        return failing_report, None

    settings = _eval_settings(
        evaluation_schedule_level="prompt",
        background_jobs_default_max_attempts=3,
        background_jobs_worker_batch_size=10,
        background_jobs_claim_lease_seconds=300,
        background_jobs_handler_timeout_seconds=30,
        background_jobs_retry_base_delay_seconds=0.0,
        background_jobs_retry_max_delay_seconds=0.0,
        background_jobs_worker_poll_interval_seconds=60,
    )
    factory = make_queue_session_factory(db_session.bind)
    queue = PostgresJobQueue(factory, settings)
    registry = JobHandlerRegistry()

    async def handler(job: BackgroundJob):
        return await scheduled_evaluation_run(
            job,
            settings=settings,
            output_dir=tmp_path,
            run_with_session=fake_run_with_session,
        )

    registry.register("scheduled_evaluation_run", handler)
    worker = JobWorker(queue=queue, registry=registry, settings=settings)

    job = await queue.enqueue(
        job_type="scheduled_evaluation_run",
        payload={"version": 1},
        max_attempts=3,
    )
    await worker.poll_once()

    updated = await queue.get(job.id)
    assert updated is not None
    assert updated.status is JobStatus.DEAD_LETTER
    assert updated.attempt_count == 1
    assert updated.last_error is not None
    assert "evaluation failed at level prompt" in updated.last_error
    assert updated.result is None


@pytest.mark.anyio
async def test_failing_eval_raises_evaluation_run_failed_error(
    tmp_path: Path,
) -> None:
    failing_report = EvalRunReport(
        dataset_path=str(DATASET),
        settings_snapshot={},
        results=[
            EvalCaseResult(
                case_id="prompt_smoke_1",
                level="prompt",
                passed=False,
                latency_ms=1,
            )
        ],
    )

    async def fake_run_with_session(
        **kwargs: object,
    ) -> tuple[EvalRunReport, int | None]:
        del kwargs
        return failing_report, None

    job = _sample_job()
    settings = _eval_settings(evaluation_schedule_level="prompt")

    with pytest.raises(
        EvaluationRunFailedError, match="evaluation failed at level prompt"
    ):
        await scheduled_evaluation_run(
            job,
            settings=settings,
            output_dir=tmp_path,
            run_with_session=fake_run_with_session,
        )


@pytest.mark.anyio
async def test_startup_reconciles_evaluation_schedule(
    db_session,
) -> None:
    if not await background_jobs_table_available(db_session):
        pytest.skip("background_jobs not available — run alembic upgrade head")
    if not await background_job_schedules_table_available(db_session):
        pytest.skip("background_job_schedules not available — run alembic upgrade head")

    factory = make_queue_session_factory(db_session.bind)
    store = PostgresJobScheduleStore(factory)
    schedule_id = await _insert_evaluation_schedule(
        store,
        status=ScheduleStatus.DISABLED,
    )
    settings = Settings(
        openai_api_key="test-key",
        background_jobs_enabled=True,
        background_jobs_worker_poll_interval_seconds=3600,
        background_jobs_scheduler_poll_interval_seconds=3600,
        background_jobs_handler_timeout_seconds=30,
        background_jobs_claim_lease_seconds=300,
        evaluation_schedule_enabled=True,
    )

    with patch("app.ai.jobs.background.get_sessionmaker", return_value=factory):
        runtime = await start_background_jobs(settings)

    assert runtime is not None
    try:
        updated = await store.get(schedule_id)
        assert updated is not None
        assert updated.status is ScheduleStatus.ENABLED
    finally:
        await stop_background_jobs(runtime)
