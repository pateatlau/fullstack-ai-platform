"""Parse LLM planner output into :class:`ExecutionPlan` models (Phase 6)."""

from __future__ import annotations

import uuid

from app.ai.agent.models.plan import ExecutionPlan, PlannedStep, StepAction
from app.ai.tools.schemas import ToolCall
from app.providers.base import ProviderToolCall, ProviderToolCompletion


def build_iteration_limit_plan(*, iteration: int) -> ExecutionPlan:
    """Return a terminal finalize plan when the iteration budget is exhausted."""
    return ExecutionPlan(
        steps=[
            PlannedStep(
                step_id=f"finalize-limit-{iteration}",
                action=StepAction.FINALIZE,
                reasoning="Iteration limit reached.",
            )
        ],
        iteration=iteration,
        is_final=True,
    )


def build_no_tools_finalize_plan(*, iteration: int) -> ExecutionPlan:
    """Return a finalize plan when no tools are available for planning."""
    return ExecutionPlan(
        steps=[
            PlannedStep(
                step_id=f"finalize-{iteration}",
                action=StepAction.FINALIZE,
                reasoning="No tools available; proceeding to final answer.",
            )
        ],
        iteration=iteration,
        is_final=True,
    )


def parse_tool_completion(
    completion: ProviderToolCompletion,
    *,
    iteration: int,
) -> ExecutionPlan:
    """Convert a tool-enabled LLM completion into the next execution plan."""
    if completion.tool_calls:
        tool_calls = [
            _provider_tool_call_to_tool_call(call) for call in completion.tool_calls
        ]
        return ExecutionPlan(
            steps=[
                PlannedStep(
                    step_id=f"tool-{iteration}-{uuid.uuid4().hex[:8]}",
                    action=StepAction.TOOL_CALL,
                    tool_calls=tool_calls,
                    reasoning=completion.content,
                )
            ],
            iteration=iteration,
            is_final=False,
        )

    return ExecutionPlan(
        steps=[
            PlannedStep(
                step_id=f"finalize-{iteration}",
                action=StepAction.FINALIZE,
                reasoning=completion.content,
            )
        ],
        iteration=iteration,
        is_final=True,
    )


def _provider_tool_call_to_tool_call(call: ProviderToolCall) -> ToolCall:
    return ToolCall(
        name=call.name,
        arguments=dict(call.arguments),
        call_id=call.id,
    )
