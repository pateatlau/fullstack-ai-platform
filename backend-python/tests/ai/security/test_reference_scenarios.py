"""End-to-end reference checks for the Phase 10 security controls."""

from __future__ import annotations

import pytest

from app.ai.evaluation.datasets import SECURITY_SCENARIOS, EvalCase, SecurityScenario
from app.ai.evaluation.security_scenarios import (
    SecurityScenarioOutcome,
    run_security_reference_scenario,
)


@pytest.mark.anyio
@pytest.mark.parametrize("scenario", sorted(SECURITY_SCENARIOS))
async def test_security_reference_scenario(scenario: SecurityScenario) -> None:
    outcome = await run_security_reference_scenario(
        EvalCase(
            id=f"security_{scenario}",
            level="security",
            security_scenario=scenario,
        )
    )

    assert outcome.passed is True, outcome.error


@pytest.mark.anyio
async def test_missing_security_scenario_reports_required() -> None:
    outcome = await run_security_reference_scenario(
        EvalCase(id="security_missing", level="security")
    )

    assert outcome == SecurityScenarioOutcome(
        passed=False,
        error="security_scenario is required",
    )


@pytest.mark.anyio
async def test_unknown_security_scenario_reports_value() -> None:
    outcome = await run_security_reference_scenario(
        EvalCase(
            id="security_unknown",
            level="security",
            security_scenario="unknown",  # type: ignore[arg-type]
        )
    )

    assert outcome == SecurityScenarioOutcome(
        passed=False,
        error="unsupported security_scenario: unknown",
    )
