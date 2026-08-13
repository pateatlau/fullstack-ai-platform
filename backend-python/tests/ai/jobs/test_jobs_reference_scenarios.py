"""Background Jobs reference scenario integration tests (Epic 10 Phase 9)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ai.evaluation.datasets import load_dataset
from app.ai.evaluation.jobs_scenarios import run_jobs_reference_scenario
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


@pytest.mark.anyio
@pytest.mark.parametrize(
    "case_id",
    [
        "jobs_hitl_expiry_agent",
        "jobs_hitl_expiry_workflow",
        "jobs_orphan_sweep_resume",
        "jobs_workflow_retention",
        "jobs_rag_indexing",
        "jobs_scheduled_eval",
    ],
)
async def test_reference_scenario_passes(db_session, case_id: str) -> None:
    dataset = load_dataset(DATASET)
    case = next(item for item in dataset.cases if item.id == case_id)
    outcome = await run_jobs_reference_scenario(
        case,
        session=db_session,
        settings=_settings(),
    )
    assert outcome.passed, outcome.error
