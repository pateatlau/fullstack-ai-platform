"""Finalize agent executions and produce streamed responses (Phase 8)."""

from __future__ import annotations

from dataclasses import dataclass

from app.ai.agent.executor.llm_step import (
    emit_answer_stream_start,
    emit_final_content_as_tokens,
    stream_final_answer,
)
from app.ai.agent.interfaces.streaming import StreamPublisher
from app.ai.agent.models.plan import ExecutionPlan, PlannedStep, StepAction
from app.ai.agent.models.request import AgentRequest
from app.ai.agent.scratchpad.scratchpad import Scratchpad
from app.providers.base import LLMProvider

# V1.1 parity — matches ``tool_chat_service._TOOL_ITERATION_LIMIT_MESSAGE``.
ITERATION_LIMIT_MESSAGE = (
    "I reached the tool-use limit for this request. "
    "Please try a simpler question or ask again."
)


@dataclass(frozen=True, slots=True)
class FinalizeResult:
    """Outcome of finalizing one agent execution."""

    content: str
    finish_reason: str


async def finalize_execution(
    plan: ExecutionPlan,
    *,
    request: AgentRequest,
    scratchpad: Scratchpad,
    provider: LLMProvider,
    execution_id: str,
    publisher: StreamPublisher,
    last_planner_content: str | None = None,
) -> FinalizeResult:
    """Produce the final user-facing answer and stream token events."""
    await emit_answer_stream_start(execution_id, publisher)
    step = _resolve_finalize_step(plan)
    if _is_iteration_limit_plan(step):
        content = last_planner_content or ITERATION_LIMIT_MESSAGE
        await emit_final_content_as_tokens(
            content=content,
            execution_id=execution_id,
            publisher=publisher,
        )
        return FinalizeResult(content=content, finish_reason="tool_iteration_cap")

    if _is_no_tools_plan(step):
        content = await stream_final_answer(
            provider,
            request=request,
            scratchpad=scratchpad,
            execution_id=execution_id,
            publisher=publisher,
        )
        return FinalizeResult(content=content, finish_reason="stop")

    content = (step.reasoning or "").strip()
    if not content:
        content = await stream_final_answer(
            provider,
            request=request,
            scratchpad=scratchpad,
            execution_id=execution_id,
            publisher=publisher,
        )
        return FinalizeResult(content=content, finish_reason="stop")

    await emit_final_content_as_tokens(
        content=content,
        execution_id=execution_id,
        publisher=publisher,
    )
    return FinalizeResult(content=content, finish_reason="stop")


def _resolve_finalize_step(plan: ExecutionPlan) -> PlannedStep:
    for step in plan.steps:
        if step.action == StepAction.FINALIZE:
            return step
    return plan.steps[0]


def _is_iteration_limit_plan(step: PlannedStep) -> bool:
    return step.step_id.startswith("finalize-limit-")


def _is_no_tools_plan(step: PlannedStep) -> bool:
    return (
        step.step_id.startswith("finalize-")
        and not step.step_id.startswith("finalize-limit-")
        and step.reasoning is not None
        and "no tools available" in step.reasoning.lower()
    )
