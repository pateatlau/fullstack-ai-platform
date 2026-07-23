"""Streaming publisher protocol (public API — stable after Phase 1)."""

from __future__ import annotations

from typing import Protocol

from app.ai.agent.models.events import AgentStreamEvent


class StreamPublisher(Protocol):
    """Publishes typed agent progress events (transport-agnostic)."""

    async def publish(self, event: AgentStreamEvent) -> None: ...

    async def close(self) -> None: ...
