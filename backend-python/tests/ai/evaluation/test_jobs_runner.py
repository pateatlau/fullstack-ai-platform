"""Tests for JobsEvalRunner."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ai.evaluation.datasets import EvalCase, load_dataset
from app.ai.evaluation.runners import JobsEvalRunner, pgvector_available
from app.core.config import Settings

DATASET = Path("tests/data/evaluation/sample.yaml")


def _settings(**overrides: object) -> Settings:
    base = {
        "openai_api_key": "test-key",
        "background_jobs_enabled": True,
        "hitl_enabled": True,
        "workflow_engine_enabled": True,
        "evaluation_schedule_level": "prompt",
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _case(**overrides: object) -> EvalCase:
    base: dict[str, object] = {
        "id": "jobs_hitl_expiry_agent",
        "level": "jobs",
        "job_type": "hitl_approval_expiry_sweep",
        "job_scenario": "hitl_expiry_agent",
    }
    base.update(overrides)
    return EvalCase(**base)  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_jobs_eval_runner_skips_when_flag_off() -> None:
    runner = JobsEvalRunner(settings=_settings(background_jobs_enabled=False))
    result = await runner.run_case(_case())

    assert result.skipped is True
    assert result.skip_reason == "BACKGROUND_JOBS_ENABLED=false"


@pytest.mark.anyio
async def test_jobs_eval_runner_skips_without_postgres() -> None:
    runner = JobsEvalRunner(settings=_settings(), session=None)
    result = await runner.run_case(_case())

    assert result.skipped is True
    assert "Postgres not available" in (result.skip_reason or "")


@pytest.mark.anyio
async def test_dataset_parses_jobs_cases() -> None:
    dataset = load_dataset(DATASET)
    jobs_cases = [case for case in dataset.cases if case.level == "jobs"]
    assert len(jobs_cases) == 6
    scenarios = {case.job_scenario for case in jobs_cases}
    assert scenarios == {
        "hitl_expiry_agent",
        "hitl_expiry_workflow",
        "orphan_sweep_resume",
        "workflow_retention",
        "rag_indexing",
        "scheduled_eval",
    }


@pytest.mark.anyio
async def test_jobs_eval_runner_hitl_expiry_agent(db_session) -> None:
    runner = JobsEvalRunner(settings=_settings(), session=db_session)
    result = await runner.run_case(_case())

    assert result.skipped is False
    assert result.passed is True


@pytest.mark.anyio
async def test_jobs_eval_runner_scheduled_eval_offline(db_session) -> None:
    runner = JobsEvalRunner(settings=_settings(), session=db_session)
    result = await runner.run_case(
        _case(
            id="jobs_scheduled_eval",
            job_type="scheduled_evaluation_run",
            job_scenario="scheduled_eval",
        )
    )

    assert result.skipped is False
    assert result.passed is True


@pytest.mark.anyio
async def test_jobs_eval_runner_rag_indexing(db_session) -> None:
    if not await pgvector_available(db_session):
        pytest.skip("pgvector extension not available")

    runner = JobsEvalRunner(settings=_settings(), session=db_session)
    result = await runner.run_case(
        _case(
            id="jobs_rag_indexing",
            job_type="rag_document_indexing",
            job_scenario="rag_indexing",
        )
    )

    assert result.skipped is False
    assert result.passed is True
