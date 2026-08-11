"""Tests for HitlEvalRunner."""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ai.evaluation.datasets import EvalCase, load_dataset
from app.ai.evaluation.runners import HitlEvalRunner, pgvector_available
from app.ai.prompts.manager import create_prompt_manager
from app.core.config import Settings

DATASET = Path("tests/data/evaluation/sample.yaml")


def _settings(**overrides: object) -> Settings:
    base = {
        "openai_api_key": "test-key",
        "hitl_enabled": True,
        "agent_runtime_enabled": True,
        "workflow_engine_enabled": True,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _agent_case(**overrides: object) -> EvalCase:
    base: dict[str, object] = {
        "id": "hitl_agent_approve",
        "level": "hitl",
        "hitl_surface": "agent",
        "hitl_decision": "approve",
        "goal": "Send a notification",
        "expected_outcome": "Notification sent.",
    }
    base.update(overrides)
    return EvalCase(**base)  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_hitl_eval_runner_skips_when_flag_off() -> None:
    runner = HitlEvalRunner(
        settings=_settings(hitl_enabled=False),
        prompt_manager=create_prompt_manager(),
    )
    result = await runner.run_case(_agent_case())

    assert result.skipped is True
    assert result.skip_reason == "HITL_ENABLED=false"


@pytest.mark.anyio
async def test_hitl_eval_runner_agent_approve_passes() -> None:
    runner = HitlEvalRunner(
        settings=_settings(hitl_required_tool_names=["send_notification"]),
        prompt_manager=create_prompt_manager(),
    )
    result = await runner.run_case(_agent_case())

    assert result.skipped is False
    assert result.passed is True


@pytest.mark.anyio
async def test_hitl_eval_runner_workflow_skips_without_postgres() -> None:
    runner = HitlEvalRunner(
        settings=_settings(),
        prompt_manager=create_prompt_manager(),
        session=None,
    )
    dataset = load_dataset(DATASET)
    workflow_case = next(
        case for case in dataset.cases if case.id == "hitl_workflow_approve_edits"
    )
    result = await runner.run_case(workflow_case)

    assert result.skipped is True
    assert "Postgres not available" in (result.skip_reason or "")


@pytest.mark.anyio
async def test_dataset_parses_hitl_cases() -> None:
    dataset = load_dataset(DATASET)
    hitl_cases = [case for case in dataset.cases if case.level == "hitl"]
    assert len(hitl_cases) == 5
    surfaces = {case.hitl_surface for case in hitl_cases}
    assert surfaces == {"agent", "workflow"}


@pytest.mark.anyio
async def test_hitl_eval_runner_workflow_skips_when_engine_disabled() -> None:
    session = AsyncMock()
    runner = HitlEvalRunner(
        settings=_settings(workflow_engine_enabled=False),
        prompt_manager=create_prompt_manager(),
        session=session,
    )
    dataset = load_dataset(DATASET)
    workflow_case = next(
        case for case in dataset.cases if case.id == "hitl_workflow_reject"
    )
    result = await runner.run_case(workflow_case)

    assert result.skipped is True
    assert result.skip_reason == "WORKFLOW_ENGINE_ENABLED=false"


@pytest.mark.anyio
async def test_hitl_eval_runner_workflow_case(db_session) -> None:
    if not await pgvector_available(db_session):
        pytest.skip("pgvector extension not available")

    runner = HitlEvalRunner(
        settings=_settings(),
        prompt_manager=create_prompt_manager(),
        session=db_session,
    )
    dataset = load_dataset(DATASET)
    workflow_case = next(
        case for case in dataset.cases if case.id == "hitl_workflow_approve_edits"
    )
    result = await runner.run_case(workflow_case)

    assert result.skipped is False
    assert result.passed is True
    assert result.terminal_status == "completed"


@pytest.mark.anyio
async def test_hitl_eval_runner_workflow_returns_failed_result_when_cleanup_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AsyncMock()
    manager = MagicMock()
    manager.create_definition = AsyncMock(side_effect=RuntimeError("create failed"))
    cleanup = AsyncMock(side_effect=RuntimeError("cleanup failed"))
    monkeypatch.setattr(
        "app.ai.evaluation.runners._cleanup_eval_workflow_owner",
        cleanup,
    )

    async def fake_create_user(self: HitlEvalRunner) -> uuid.UUID:
        return uuid.uuid4()

    monkeypatch.setattr(HitlEvalRunner, "_create_user", fake_create_user)
    monkeypatch.setattr(
        "app.ai.evaluation.runners._build_hitl_eval_workflow_manager",
        lambda **_kwargs: manager,
    )
    monkeypatch.setattr(
        "app.ai.evaluation.runners.pgvector_available",
        AsyncMock(return_value=True),
    )

    dataset = load_dataset(DATASET)
    workflow_case = next(
        case for case in dataset.cases if case.id == "hitl_workflow_reject"
    )
    runner = HitlEvalRunner(
        settings=_settings(),
        prompt_manager=create_prompt_manager(),
        session=session,
    )
    result = await runner.run_case(workflow_case)

    assert result.passed is False
    assert result.error == "create failed"
    session.rollback.assert_awaited_once()
    cleanup.assert_awaited_once()
