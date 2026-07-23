"""Agent entry-point protocol (public API — stable after Phase 1)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from app.ai.agent.models.context import AgentContext
from app.ai.agent.models.events import AgentStreamEvent
from app.ai.agent.models.request import AgentRequest
from app.ai.agent.models.response import AgentResponse


class Agent(Protocol):
    """Lifecycle entry point: run to completion or stream typed progress events."""

    async def run(
        self,
        request: AgentRequest,
        context: AgentContext,
    ) -> AgentResponse: ...

    def stream(
        self,
        request: AgentRequest,
        context: AgentContext,
    ) -> AsyncIterator[AgentStreamEvent]: ...
