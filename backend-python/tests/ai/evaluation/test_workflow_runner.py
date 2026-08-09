"""Tests for WorkflowEvalRunner."""

from __future__ import annotations

import datetime
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.ai.evaluation.datasets import EvalCase
from app.ai.evaluation.runners import WorkflowEvalRunner
from app.ai.workflow.models import (
    DefinitionStatus,
    NodeType,
    RunStatus,
    WorkflowContext,
    WorkflowRun,
)
from app.core.config import Settings

_NOW = datetime.datetime.now(datetime.UTC)


def _settings(**overrides: object) -> Settings:
    base = {
        "openai_api_key": "test-key",
        "workflow_engine_enabled": True,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _workflow_case(**overrides: object) -> EvalCase:
    base = {
        "id": "workflow_echo",
        "level": "workflow",
        "expected_terminal_status": "completed",
        "workflow_definition": {
            "name": "Eval Workflow",
            "entry_node_id": "start",
            "nodes": [
                {
                    "id": "start",
                    "type": "task",
                    "config": {
                        "tool_name": "echo",
                        "arguments_template": {"message": "hello"},
                    },
                },
                {"id": "end", "type": "terminal", "config": {}},
            ],
            "edges": [{"id": "e1", "from_node_id": "start", "to_node_id": "end"}],
        },
    }
    base.update(overrides)
    return EvalCase(**base)  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_workflow_eval_runner_passes_with_expected_terminal_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AsyncMock()
    owner_id = uuid.uuid4()
    run_id = uuid.uuid4()
    definition_id = uuid.uuid4()
    manager = MagicMock()
    manager.create_definition = AsyncMock(
        return_value=MagicMock(id=definition_id),
    )
    manager.start_run = AsyncMock(
        return_value=WorkflowRun(
            id=run_id,
            workflow_definition_id=definition_id,
            owner_id=owner_id,
            idempotency_key="eval-key",
            status=RunStatus.RUNNING,
            context=WorkflowContext(),
            current_node_ids=[],
            checkpoint_version=0,
            created_at=_NOW,
            updated_at=_NOW,
            started_at=_NOW,
        )
    )
    manager.get_run = AsyncMock(
        return_value=WorkflowRun(
            id=run_id,
            workflow_definition_id=definition_id,
            owner_id=owner_id,
            idempotency_key="eval-key",
            status=RunStatus.COMPLETED,
            context=WorkflowContext(),
            current_node_ids=[],
            checkpoint_version=1,
            created_at=_NOW,
            updated_at=_NOW,
            started_at=_NOW,
        )
    )
    manager._last_scheduled_run_task = None

    async def fake_create_user(self: WorkflowEvalRunner) -> uuid.UUID:
        return owner_id

    monkeypatch.setattr(WorkflowEvalRunner, "_create_user", fake_create_user)
    monkeypatch.setattr(
        "app.ai.evaluation.runners._build_eval_workflow_manager",
        lambda **_kwargs: manager,
    )
    monkeypatch.setattr(
        "app.ai.evaluation.runners._await_scheduled_run",
        AsyncMock(),
    )

    runner = WorkflowEvalRunner(session=session, settings=_settings())
    result = await runner.run_case(_workflow_case())

    assert result.passed is True
    assert result.terminal_status == "completed"
    assert result.model is None
    assert result.prompt_version is None


@pytest.mark.anyio
async def test_workflow_eval_runner_fails_on_terminal_status_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AsyncMock()
    owner_id = uuid.uuid4()
    run_id = uuid.uuid4()
    definition_id = uuid.uuid4()
    manager = MagicMock()
    manager.create_definition = AsyncMock(return_value=MagicMock(id=definition_id))
    manager.start_run = AsyncMock(
        return_value=WorkflowRun(
            id=run_id,
            workflow_definition_id=definition_id,
            owner_id=owner_id,
            idempotency_key="eval-key",
            status=RunStatus.RUNNING,
            context=WorkflowContext(),
            current_node_ids=[],
            checkpoint_version=0,
            created_at=_NOW,
            updated_at=_NOW,
            started_at=_NOW,
        )
    )
    manager.get_run = AsyncMock(
        return_value=WorkflowRun(
            id=run_id,
            workflow_definition_id=definition_id,
            owner_id=owner_id,
            idempotency_key="eval-key",
            status=RunStatus.FAILED,
            context=WorkflowContext(),
            current_node_ids=[],
            checkpoint_version=1,
            created_at=_NOW,
            updated_at=_NOW,
            started_at=_NOW,
        )
    )
    manager._last_scheduled_run_task = None

    async def fake_create_user(self: WorkflowEvalRunner) -> uuid.UUID:
        return owner_id

    monkeypatch.setattr(WorkflowEvalRunner, "_create_user", fake_create_user)
    monkeypatch.setattr(
        "app.ai.evaluation.runners._build_eval_workflow_manager",
        lambda **_kwargs: manager,
    )
    monkeypatch.setattr(
        "app.ai.evaluation.runners._await_scheduled_run",
        AsyncMock(),
    )

    runner = WorkflowEvalRunner(session=session, settings=_settings())
    result = await runner.run_case(_workflow_case())

    assert result.passed is False
    assert result.terminal_status == "failed"


@pytest.mark.anyio
async def test_workflow_eval_runner_skips_when_flag_off() -> None:
    session = AsyncMock()
    runner = WorkflowEvalRunner(
        session=session,
        settings=_settings(workflow_engine_enabled=False),
    )

    result = await runner.run_case(_workflow_case())

    assert result.skipped is True
    assert result.skip_reason == "WORKFLOW_ENGINE_ENABLED=false"


@pytest.mark.anyio
async def test_workflow_eval_runner_populates_llm_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AsyncMock()
    owner_id = uuid.uuid4()
    run_id = uuid.uuid4()
    definition_id = uuid.uuid4()
    manager = MagicMock()
    manager.create_definition = AsyncMock(return_value=MagicMock(id=definition_id))
    manager.start_run = AsyncMock(
        return_value=WorkflowRun(
            id=run_id,
            workflow_definition_id=definition_id,
            owner_id=owner_id,
            idempotency_key="eval-key",
            status=RunStatus.RUNNING,
            context=WorkflowContext(),
            current_node_ids=[],
            checkpoint_version=0,
            created_at=_NOW,
            updated_at=_NOW,
            started_at=_NOW,
        )
    )
    manager.get_run = AsyncMock(
        return_value=WorkflowRun(
            id=run_id,
            workflow_definition_id=definition_id,
            owner_id=owner_id,
            idempotency_key="eval-key",
            status=RunStatus.COMPLETED,
            context=WorkflowContext(),
            current_node_ids=[],
            checkpoint_version=1,
            created_at=_NOW,
            updated_at=_NOW,
            started_at=_NOW,
        )
    )
    manager._last_scheduled_run_task = None

    async def fake_create_user(self: WorkflowEvalRunner) -> uuid.UUID:
        return owner_id

    captured_definition = None

    async def fake_create_definition(definition):  # type: ignore[no-untyped-def]
        nonlocal captured_definition
        captured_definition = definition
        return MagicMock(id=definition_id)

    manager.create_definition = fake_create_definition

    monkeypatch.setattr(WorkflowEvalRunner, "_create_user", fake_create_user)
    monkeypatch.setattr(
        "app.ai.evaluation.runners._build_eval_workflow_manager",
        lambda **_kwargs: manager,
    )
    monkeypatch.setattr(
        "app.ai.evaluation.runners._await_scheduled_run",
        AsyncMock(),
    )

    runner = WorkflowEvalRunner(session=session, settings=_settings())
    case = _workflow_case(
        workflow_definition={
            "name": "LLM Workflow",
            "entry_node_id": "llm",
            "nodes": [
                {
                    "id": "llm",
                    "type": "llm",
                    "config": {
                        "model": "gpt-4o-mini",
                        "prompt_version": "rag/answer/1",
                    },
                },
                {"id": "end", "type": "terminal", "config": {}},
            ],
            "edges": [{"id": "e1", "from_node_id": "llm", "to_node_id": "end"}],
        }
    )
    result = await runner.run_case(case)

    assert result.passed is True
    assert result.model == "gpt-4o-mini"
    assert result.prompt_version == "rag/answer/1"
    assert captured_definition is not None
    assert captured_definition.nodes[0].type is NodeType.LLM
    assert captured_definition.status is DefinitionStatus.ACTIVE


@pytest.mark.anyio
async def test_workflow_eval_runner_rolls_back_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AsyncMock()
    manager = MagicMock()
    manager.create_definition = AsyncMock(side_effect=RuntimeError("create failed"))
    cleanup = AsyncMock()
    monkeypatch.setattr(
        "app.ai.evaluation.runners._cleanup_eval_workflow_owner",
        cleanup,
    )

    async def fake_create_user(self: WorkflowEvalRunner) -> uuid.UUID:
        return uuid.uuid4()

    monkeypatch.setattr(WorkflowEvalRunner, "_create_user", fake_create_user)
    monkeypatch.setattr(
        "app.ai.evaluation.runners._build_eval_workflow_manager",
        lambda **_kwargs: manager,
    )

    runner = WorkflowEvalRunner(session=session, settings=_settings())
    result = await runner.run_case(_workflow_case())

    assert result.passed is False
    assert result.error == "create failed"
    session.rollback.assert_awaited_once()
    cleanup.assert_awaited_once()
