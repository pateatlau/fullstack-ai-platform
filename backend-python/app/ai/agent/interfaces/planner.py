"""Planner protocol (public API — stable after Phase 1)."""

from __future__ import annotations

from typing import Protocol

from app.ai.agent.models.context import AgentContext
from app.ai.agent.models.plan import ExecutionPlan
from app.ai.agent.models.request import AgentRequest


class Planner(Protocol):
    """ReAct-style iterative planner producing the next :class:`ExecutionPlan`."""

    async def plan_next(
        self,
        request: AgentRequest,
        context: AgentContext,
        *,
        iteration: int,
    ) -> ExecutionPlan: ...
