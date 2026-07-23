"""Executor protocol (public API — stable after Phase 1)."""

from __future__ import annotations

from typing import Protocol

from app.ai.agent.models.context import AgentContext
from app.ai.agent.models.plan import ExecutionPlan, PlannedStep
from app.ai.agent.models.request import AgentRequest
from app.ai.agent.models.response import AgentResponse


class Executor(Protocol):
    """Runs planned steps (tools, LLM rounds) and assembles an :class:`AgentResponse`."""

    async def execute_plan(
        self,
        plan: ExecutionPlan,
        request: AgentRequest,
        context: AgentContext,
    ) -> AgentResponse: ...

    async def execute_step(
        self,
        step: PlannedStep,
        request: AgentRequest,
        context: AgentContext,
    ) -> object: ...
