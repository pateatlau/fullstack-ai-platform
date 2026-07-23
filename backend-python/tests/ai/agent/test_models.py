"""Tests for agent runtime models (Phase 1)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.ai.agent import (
    AgentConfig,
    AgentContext,
    AgentMessage,
    AgentRequest,
    AgentResponse,
    AgentStreamEvent,
    AgentStreamEventType,
    ExecutionPlan,
    PlannedStep,
    StepAction,
)
from app.ai.tools.schemas import ToolCall


def test_agent_config_defaults_match_part_i() -> None:
    config = AgentConfig()
    assert config.max_iterations == 5
    assert config.reflection_enabled is False
    assert config.max_reflections == 2
    assert config.max_retries == 3
    assert config.retry_base_delay_seconds == 1.0
    assert config.parallel_tools_enabled is False
    assert config.timeout_seconds is None


def test_agent_request_requires_messages_and_model() -> None:
    request = AgentRequest(
        messages=[AgentMessage(role="user", content="Hello")],
        model="gpt-4o-mini",
    )
    assert request.provider is None
    assert request.temperature == 0.7
    assert request.config is None


def test_agent_request_rejects_empty_messages() -> None:
    with pytest.raises(ValidationError):
        AgentRequest(messages=[], model="gpt-4o-mini")


def test_agent_context_generates_execution_id() -> None:
    context = AgentContext()
    assert len(context.execution_id) == 32
    assert context.request_id is None
    assert context.allowed_tool_names is None


def test_execution_plan_and_planned_step() -> None:
    step = PlannedStep(
        step_id="step-1",
        action=StepAction.TOOL_CALL,
        tool_calls=[ToolCall(name="web_search", arguments={"query": "test"})],
        depends_on=["step-0"],
        reasoning="Need current information.",
    )
    plan = ExecutionPlan(steps=[step], iteration=1, is_final=False)
    assert plan.steps[0].action == StepAction.TOOL_CALL
    assert plan.steps[0].tool_calls[0].name == "web_search"


def test_execution_plan_requires_at_least_one_step() -> None:
    with pytest.raises(ValidationError):
        ExecutionPlan(steps=[], iteration=0)


def test_agent_response_defaults() -> None:
    response = AgentResponse(content="Done.")
    assert response.tools_used == []
    assert response.iterations == 0
    assert response.finish_reason is None


def test_agent_stream_event_types_cover_part_i_strategy() -> None:
    expected = {
        "start",
        "planning",
        "tool_start",
        "tool_end",
        "token",
        "reflection",
        "complete",
        "error",
    }
    assert {member.value for member in AgentStreamEventType} == expected


def test_agent_stream_event_round_trip() -> None:
    event = AgentStreamEvent(
        type=AgentStreamEventType.PLANNING,
        execution_id="abc123",
        payload={"iteration": 1},
    )
    restored = AgentStreamEvent.model_validate(event.model_dump())
    assert restored.type == AgentStreamEventType.PLANNING
    assert restored.payload["iteration"] == 1


def test_step_action_values_are_stable_strings() -> None:
    assert StepAction.TOOL_CALL == "tool_call"
    assert StepAction.LLM == "llm"
    assert StepAction.FINALIZE == "finalize"
