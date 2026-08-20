"""End-to-end reference checks for the Phase 10 security controls."""

from __future__ import annotations

import pytest

from app.ai.evaluation.datasets import EvalCase
from app.ai.evaluation.security_scenarios import run_security_reference_scenario


@pytest.mark.anyio
@pytest.mark.parametrize(
    "scenario",
    [
        "destructive_tool_rbac",
        "hitl_stage_rbac",
        "jobs_visibility_rbac",
        "guardrail_block",
        "guardrail_flag",
        "role_rate_limit",
    ],
)
async def test_security_reference_scenario(scenario: str) -> None:
    outcome = await run_security_reference_scenario(
        EvalCase(
            id=f"security_{scenario}",
            level="security",
            security_scenario=scenario,  # type: ignore[arg-type]
        )
    )

    assert outcome.passed is True, outcome.error
