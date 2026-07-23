"""Planner output models (public API — stable after Phase 1)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from app.ai.tools.schemas import ToolCall


class StepAction(StrEnum):
    """Next action chosen by the planner for one iteration."""

    TOOL_CALL = "tool_call"
    LLM = "llm"
    FINALIZE = "finalize"


class PlannedStep(BaseModel):
    """One executable step within an :class:`ExecutionPlan`."""

    step_id: str
    action: StepAction
    tool_calls: list[ToolCall] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    reasoning: str | None = None


class ExecutionPlan(BaseModel):
    """Planner output describing the next unit of work."""

    steps: list[PlannedStep] = Field(min_length=1)
    iteration: int = Field(default=0, ge=0)
    is_final: bool = False
