"""Tests for agent execution state management (Phase 2)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.ai.agent.exceptions import AgentIterationLimitError
from app.ai.agent.models.config import AgentConfig
from app.ai.agent.models.context import AgentContext
from app.ai.agent.models.state import AgentExecutionState, AgentExecutionStatus
from app.ai.agent.state.manager import AgentStateManager, InvalidStateTransitionError


def test_create_initial_state_uses_context_and_config_defaults() -> None:
    context = AgentContext(
        execution_id="exec-123",
        metadata={"request_source": "test", "api_key": "secret-value"},
    )
    config = AgentConfig(max_iterations=3)

    state = AgentStateManager.create_initial_state(context, config)

    assert state.execution_id == "exec-123"
    assert state.status == AgentExecutionStatus.CREATED
    assert state.max_iterations == 3
    assert state.current_iteration == 0
    assert state.metadata["request_source"] == "test"
    assert state.metadata["api_key"] == "secret-value"


def test_full_happy_path_lifecycle_created_to_completed() -> None:
    state = AgentExecutionState(execution_id="exec-1")

    state = AgentStateManager.transition(state, AgentExecutionStatus.PLANNING)
    state = AgentStateManager.begin_iteration(state)
    state = AgentStateManager.transition(state, AgentExecutionStatus.EXECUTING)
    state = AgentStateManager.record_tool_used(state, "web_search")
    state = AgentStateManager.transition(state, AgentExecutionStatus.REFLECTING)
    state = AgentStateManager.record_reflection(state)
    state = AgentStateManager.transition(state, AgentExecutionStatus.PLANNING)
    state = AgentStateManager.begin_iteration(state)
    state = AgentStateManager.transition(state, AgentExecutionStatus.EXECUTING)
    state = AgentStateManager.transition(state, AgentExecutionStatus.COMPLETED)

    assert state.status == AgentExecutionStatus.COMPLETED
    assert state.current_iteration == 2
    assert state.tools_used == ["web_search"]
    assert state.reflection_count == 1


def test_direct_finalize_path_planning_to_completed() -> None:
    state = AgentExecutionState(execution_id="exec-2")
    state = AgentStateManager.transition(state, AgentExecutionStatus.PLANNING)
    state = AgentStateManager.transition(state, AgentExecutionStatus.COMPLETED)

    assert state.status == AgentExecutionStatus.COMPLETED


def test_invalid_transition_raises() -> None:
    state = AgentExecutionState(execution_id="exec-3")

    with pytest.raises(InvalidStateTransitionError) as exc_info:
        AgentStateManager.transition(state, AgentExecutionStatus.EXECUTING)

    assert exc_info.value.current == AgentExecutionStatus.CREATED
    assert exc_info.value.target == AgentExecutionStatus.EXECUTING


def test_terminal_states_reject_further_transitions() -> None:
    completed = AgentExecutionState(
        execution_id="exec-4",
        status=AgentExecutionStatus.COMPLETED,
    )
    failed = AgentExecutionState(
        execution_id="exec-5",
        status=AgentExecutionStatus.FAILED,
    )

    with pytest.raises(InvalidStateTransitionError):
        AgentStateManager.transition(completed, AgentExecutionStatus.PLANNING)

    with pytest.raises(InvalidStateTransitionError):
        AgentStateManager.transition(failed, AgentExecutionStatus.PLANNING)


def test_failed_transition_preserves_error_message() -> None:
    state = AgentExecutionState(execution_id="exec-6")
    state = AgentStateManager.transition(state, AgentExecutionStatus.PLANNING)
    state = AgentStateManager.transition(
        state,
        AgentExecutionStatus.FAILED,
        error_message="planner timeout",
    )

    assert state.status == AgentExecutionStatus.FAILED
    assert state.error_message == "planner timeout"


def test_to_dict_excludes_secret_metadata_keys() -> None:
    state = AgentExecutionState(
        execution_id="exec-7",
        tools_used=["web_search"],
        metadata={
            "trace_id": "abc",
            "api_key": "should-not-appear",
            "user_token": "also-hidden",
        },
    )

    payload = state.to_dict()
    metadata = payload["metadata"]
    assert isinstance(metadata, dict)

    assert payload["execution_id"] == "exec-7"
    assert payload["tools_used"] == ["web_search"]
    assert metadata == {"trace_id": "abc"}
    assert "api_key" not in metadata
    assert "user_token" not in metadata


def test_iteration_limit_helpers() -> None:
    state = AgentExecutionState(execution_id="exec-8", max_iterations=2)

    assert state.has_remaining_iterations() is True
    assert state.is_at_iteration_limit() is False

    state = AgentStateManager.begin_iteration(state)
    assert state.current_iteration == 1
    assert state.iteration_limit_reached is False

    state = AgentStateManager.begin_iteration(state)
    assert state.current_iteration == 2
    assert state.iteration_limit_reached is True
    assert state.is_at_iteration_limit() is True
    assert state.has_remaining_iterations() is False

    with pytest.raises(AgentIterationLimitError) as exc_info:
        AgentStateManager.begin_iteration(state)

    assert exc_info.value.max_iterations == 2


def test_mark_iteration_limit_reached() -> None:
    state = AgentExecutionState(execution_id="exec-9", max_iterations=5)
    state = AgentStateManager.mark_iteration_limit_reached(state)

    assert state.iteration_limit_reached is True
    assert state.is_at_iteration_limit() is True


def test_record_helpers_are_idempotent_for_tools_and_ignore_non_positive_parallel() -> (
    None
):
    state = AgentExecutionState(execution_id="exec-10")
    state = AgentStateManager.record_tool_used(state, "web_search")
    state = AgentStateManager.record_tool_used(state, "web_search")
    state = AgentStateManager.record_retry(state)
    state = AgentStateManager.record_parallel_tools(state, 2)
    state = AgentStateManager.record_parallel_tools(state, 0)

    assert state.tools_used == ["web_search"]
    assert state.retry_count == 1
    assert state.parallel_tools_count == 2


def test_state_modules_have_no_db_or_chat_imports() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    module_paths = [
        repo_root / "app/ai/agent/models/state.py",
        repo_root / "app/ai/agent/state/manager.py",
    ]
    forbidden_roots = ("app.services", "app.db", "app.schemas.chat", "fastapi")

    for module_path in module_paths:
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        imported_modules.update(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        for forbidden in forbidden_roots:
            assert not any(
                module == forbidden or module.startswith(f"{forbidden}.")
                for module in imported_modules
            ), f"{module_path.name} must not import {forbidden}"
