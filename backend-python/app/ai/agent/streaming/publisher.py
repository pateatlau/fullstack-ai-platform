"""Stream publisher implementations (Phase 5)."""

from __future__ import annotations

import asyncio

from app.ai.agent.models.events import AgentStreamEvent


class StreamPublisherClosedError(RuntimeError):
    """Raised when publishing to a closed stream publisher."""


class InMemoryStreamPublisher:
    """Collects events in memory for tests and synchronous inspection."""

    def __init__(self) -> None:
        self.events: list[AgentStreamEvent] = []
        self._closed = False

    async def publish(self, event: AgentStreamEvent) -> None:
        if self._closed:
            raise StreamPublisherClosedError("InMemoryStreamPublisher is closed")
        self.events.append(event)

    async def close(self) -> None:
        self._closed = True


class QueueStreamPublisher:
    """Pushes events onto an asyncio queue for async consumers."""

    def __init__(
        self, queue: asyncio.Queue[AgentStreamEvent | None] | None = None
    ) -> None:
        self._queue = queue or asyncio.Queue()
        self._closed = False

    @property
    def queue(self) -> asyncio.Queue[AgentStreamEvent | None]:
        return self._queue

    async def publish(self, event: AgentStreamEvent) -> None:
        if self._closed:
            raise StreamPublisherClosedError("QueueStreamPublisher is closed")
        await self._queue.put(event)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._queue.put(None)


class NoOpStreamPublisher:
    """Discards all events (e.g. non-streaming agent runs)."""

    async def publish(self, event: AgentStreamEvent) -> None:
        _ = event

    async def close(self) -> None:
        return None
