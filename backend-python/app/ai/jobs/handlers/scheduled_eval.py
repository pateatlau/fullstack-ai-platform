"""Scheduled evaluation run handler (Epic 10 Phase 6)."""

from __future__ import annotations

import uuid
from pathlib import Path

from app.ai.evaluation.cli import (
    DEFAULT_DATASET,
    _levels_to_run,
    _run_with_session,
)
from app.ai.evaluation.report import EvalRunReport, write_json_report
from app.ai.jobs.models import BackgroundJob, JobResult, ScheduleStatus
from app.ai.jobs.retry import NonRetryableJobError
from app.ai.jobs.schedule_store import PostgresJobScheduleStore
from app.core.config import Settings

_PAYLOAD_VERSION = 1
SCHEDULED_EVALUATION_SCHEDULE_NAME = "scheduled-evaluation-run"
DEFAULT_SCHEDULED_EVAL_OUTPUT_DIR = Path(".eval")


class EvaluationRunFailedError(Exception):
    """Raised when scheduled evaluation completes but cases failed."""


def _job_result_from_report(
    report: EvalRunReport,
    *,
    level: str,
    report_path: Path,
) -> JobResult:
    passed = report.passed_count
    failed = report.failed_count
    skipped = report.skipped_count
    executed = passed + failed
    pass_rate_pct = int(round(passed / executed * 100)) if executed else 100

    return JobResult(
        summary=(
            f"evaluation level={level} pass_rate={pass_rate_pct}% "
            f"(passed={passed} failed={failed} skipped={skipped})"
        ),
        counts={"passed": passed, "failed": failed, "skipped": skipped},
        ref_id=str(report_path),
    )


def _report_output_path(
    job_id: uuid.UUID,
    *,
    output_dir: Path = DEFAULT_SCHEDULED_EVAL_OUTPUT_DIR,
) -> Path:
    return output_dir / f"scheduled-eval-{job_id}.json"


async def scheduled_evaluation_run(
    job: BackgroundJob,
    *,
    settings: Settings,
    output_dir: Path = DEFAULT_SCHEDULED_EVAL_OUTPUT_DIR,
) -> JobResult:
    """Run the evaluation framework on a schedule and persist the JSON report."""
    if not settings.background_jobs_enabled:
        return JobResult(summary="background jobs disabled")

    payload = job.payload
    version = payload.get("version")
    if version != _PAYLOAD_VERSION:
        raise NonRetryableJobError(f"unsupported payload version: {version!r}")

    level = settings.evaluation_schedule_level
    levels = _levels_to_run(level)
    report, early_exit = await _run_with_session(
        settings=settings,
        dataset_path=DEFAULT_DATASET,
        levels=levels,
        use_judge=False,
        level_arg=level,
    )
    if early_exit is not None:
        raise NonRetryableJobError(
            f"evaluation prerequisites not met for level {level!r} (exit={early_exit})"
        )

    output_path = _report_output_path(job.id, output_dir=output_dir)
    write_json_report(report, output_path)

    if not report.all_passed():
        failed_ids = [
            result.case_id
            for result in report.results
            if not result.passed and not result.skipped
        ]
        preview = ", ".join(failed_ids[:5])
        suffix = "..." if len(failed_ids) > 5 else ""
        raise EvaluationRunFailedError(
            f"evaluation failed at level {level}: "
            f"{report.failed_count} case(s) failed ({preview}{suffix})"
        )

    return _job_result_from_report(report, level=level, report_path=output_path)


async def reconcile_evaluation_schedule_status(
    store: PostgresJobScheduleStore,
    settings: Settings,
) -> None:
    """Align the seeded evaluation schedule row with ``evaluation_schedule_enabled``."""
    schedule = await store.get_by_name(SCHEDULED_EVALUATION_SCHEDULE_NAME)
    if schedule is None:
        return

    desired = (
        ScheduleStatus.ENABLED
        if settings.evaluation_schedule_enabled
        else ScheduleStatus.DISABLED
    )
    if schedule.status == desired:
        return

    await store.set_status(
        schedule.id,
        expected_version=schedule.version,
        status=desired,
    )
