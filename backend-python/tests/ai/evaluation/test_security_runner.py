"""Phase 10 Security & Governance evaluation runner tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ai.evaluation.datasets import EvalCase, load_dataset
from app.ai.evaluation.runners import SecurityEvalRunner
from app.core.config import Settings

DATASET = Path("tests/data/evaluation/sample.yaml")


def _settings(*, enabled: bool = True) -> Settings:
    return Settings(
        openai_api_key="test-key",
        security_governance_enabled=enabled,
    )


@pytest.mark.anyio
async def test_security_eval_runner_skips_when_flag_off() -> None:
    case = EvalCase(
        id="security_guardrail_block",
        level="security",
        security_scenario="guardrail_block",
    )

    result = await SecurityEvalRunner(settings=_settings(enabled=False)).run_case(case)

    assert result.skipped is True
    assert result.skip_reason == "SECURITY_GOVERNANCE_ENABLED=false"


@pytest.mark.anyio
async def test_all_security_reference_scenarios_pass() -> None:
    cases = [case for case in load_dataset(DATASET).cases if case.level == "security"]
    runner = SecurityEvalRunner(settings=_settings())

    results = [await runner.run_case(case) for case in cases]

    assert len(results) == 6
    assert all(result.passed for result in results), [
        result.error for result in results
    ]
