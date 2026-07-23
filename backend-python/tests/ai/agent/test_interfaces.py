"""Tests for agent runtime Protocol interfaces (Phase 1)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from app.ai.agent import (
    Agent,
    AgentContext,
    AgentMessage,
    AgentRequest,
    AgentResponse,
    AgentStreamEvent,
    AgentStreamEventType,
    ExecutionPlan,
    Executor,
    PlannedStep,
    Planner,
    RetryPolicy,
    StepAction,
    StreamPublisher,
)
from app.ai.agent.exceptions import AgentIterationLimitError, AgentTimeoutError
from app.core.retry import is_retryable_exception


class _StubAgent:
    async def run(
        self,
        request: AgentRequest,
        context: AgentContext,
    ) -> AgentResponse:
        _ = (request, context)
        return AgentResponse(content="ok")

    async def stream(
        self,
        request: AgentRequest,
        context: AgentContext,
    ) -> AsyncIterator[AgentStreamEvent]:
        _ = (request, context)
        yield AgentStreamEvent(
            type=AgentStreamEventType.COMPLETE,
            execution_id=context.execution_id,
        )


class _StubPlanner:
    async def plan_next(
        self,
        request: AgentRequest,
        context: AgentContext,
        *,
        iteration: int,
    ) -> ExecutionPlan:
        _ = (request, context, iteration)
        return ExecutionPlan(
            steps=[
                PlannedStep(step_id="finalize-1", action=StepAction.FINALIZE),
            ],
            iteration=iteration,
            is_final=True,
        )


class _StubExecutor:
    async def execute_plan(
        self,
        plan: ExecutionPlan,
        request: AgentRequest,
        context: AgentContext,
    ) -> AgentResponse:
        _ = (plan, request, context)
        return AgentResponse(content="executed")

    async def execute_step(
        self,
        step: PlannedStep,
        request: AgentRequest,
        context: AgentContext,
    ) -> object:
        _ = (step, request, context)
        return {"status": "ok"}


class _StubRetryPolicy:
    @property
    def max_retries(self) -> int:
        return 3

    @property
    def base_delay_seconds(self) -> float:
        return 1.0

    def is_retryable(self, exc: BaseException) -> bool:
        return is_retryable_exception(exc)


class _StubStreamPublisher:
    def __init__(self) -> None:
        self.events: list[AgentStreamEvent] = []

    async def publish(self, event: AgentStreamEvent) -> None:
        self.events.append(event)

    async def close(self) -> None:
        return None


def test_public_api_exports_are_present() -> None:
    exported = {
        "Agent",
        "Planner",
        "Executor",
        "RetryPolicy",
        "StreamPublisher",
        "AgentRequest",
        "AgentContext",
        "AgentResponse",
        "ExecutionPlan",
        "PlannedStep",
        "StepAction",
        "AgentConfig",
        "AgentError",
        "AgentIterationLimitError",
        "AgentTimeoutError",
    }
    import app.ai.agent as agent_pkg

    for name in exported:
        assert hasattr(agent_pkg, name), f"missing export: {name}"


def test_stub_types_satisfy_protocols() -> None:
    agent: Agent = _StubAgent()
    planner: Planner = _StubPlanner()
    executor: Executor = _StubExecutor()
    retry_policy: RetryPolicy = _StubRetryPolicy()
    publisher: StreamPublisher = _StubStreamPublisher()

    assert agent is not None
    assert planner is not None
    assert executor is not None
    assert retry_policy.max_retries == 3
    assert publisher is not None


@pytest.mark.anyio
async def test_stub_agent_run_and_stream() -> None:
    agent = _StubAgent()
    context = AgentContext(execution_id="exec-1")
    request = AgentRequest(
        messages=[AgentMessage(role="user", content="Hi")],
        model="gpt-4o-mini",
    )

    response = await agent.run(request, context)
    assert response.content == "ok"

    events = [event async for event in agent.stream(request, context)]
    assert len(events) == 1
    assert events[0].type == AgentStreamEventType.COMPLETE


@pytest.mark.anyio
async def test_stub_planner_returns_execution_plan() -> None:
    planner = _StubPlanner()
    plan = await planner.plan_next(
        AgentRequest(
            messages=[AgentMessage(role="user", content="Search")],
            model="gpt-4o-mini",
        ),
        AgentContext(),
        iteration=0,
    )
    assert plan.is_final is True
    assert plan.steps[0].action == StepAction.FINALIZE


def test_agent_exceptions_carry_context() -> None:
    limit_error = AgentIterationLimitError(max_iterations=5)
    assert limit_error.max_iterations == 5
    assert "5" in str(limit_error)

    timeout_error = AgentTimeoutError(timeout_seconds=30)
    assert timeout_error.timeout_seconds == 30
    assert "30" in str(timeout_error)
