"""``MemoryManager`` — single orchestration entry point for the Memory subsystem.

Public API (stable after Phase 1). Retrieval (``retrieve_context``) and async
extraction (``extract_and_persist_async``) are added in Phase 2/3/6; Phase 1
wires dependency injection and pass-through record/preference accessors only.
"""

from __future__ import annotations

import uuid

from app.ai.memory.interfaces.memory_provider import MemoryProvider
from app.ai.memory.models import MemoryRecord


class MemoryManager:
    """Coordinates retrieval, persistence, and lifecycle via a ``MemoryProvider``."""

    def __init__(self, provider: MemoryProvider) -> None:
        self._provider = provider

    async def get_record(
        self, record_id: uuid.UUID, *, owner_id: uuid.UUID
    ) -> MemoryRecord | None:
        """Return an owned memory record, or ``None`` if it does not exist."""
        return await self._provider.get_record(record_id, owner_id=owner_id)

    async def delete_record(self, record_id: uuid.UUID, *, owner_id: uuid.UUID) -> None:
        """Delete an owned memory record via the configured provider."""
        await self._provider.delete_record(record_id, owner_id=owner_id)

    async def get_preference(
        self, *, user_id: uuid.UUID, key: str
    ) -> dict[str, object] | None:
        """Return a caller's structured preference value, if set."""
        return await self._provider.get_preference(user_id=user_id, key=key)

    async def set_preference(
        self, *, user_id: uuid.UUID, key: str, value: dict[str, object]
    ) -> None:
        """Upsert a caller's structured preference value."""
        await self._provider.set_preference(user_id=user_id, key=key, value=value)

    async def delete_preference(self, *, user_id: uuid.UUID, key: str) -> None:
        """Remove a caller's structured preference value."""
        await self._provider.delete_preference(user_id=user_id, key=key)
