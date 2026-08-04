"""Build canonical ``MemoryContext`` from retrieved memory subsystems.

Phase 4 loads structured user preferences. Phase 5 loads session-scoped project
memories. Phase 6 adds semantic retrieval ranking and token budgeting.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, cast

from app.ai.memory.interfaces.memory_provider import MemoryProvider
from app.ai.memory.models import MemoryContext, MemoryRecord, MemoryType
from app.ai.memory.preferences import normalize_preferences
from app.ai.memory.project import (
    map_project_id_to_session_id,
    normalize_project_memories,
    validate_project_id,
)
from app.ai.memory.semantic_retriever import RetrievalResult
from app.ai.memory.token_budget import TokenBudgetAllocator
from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.core.config import Settings

logger = get_logger(__name__)

ListActiveRecords = Callable[..., Awaitable[list[MemoryRecord]]]


class MemoryContextBuilder:
    """Combines retrieved memory inputs into a normalized ``MemoryContext``."""

    def __init__(
        self,
        provider: MemoryProvider,
        *,
        settings: Settings | None = None,
    ) -> None:
        self._provider = provider
        self._settings = settings
        self._budget_allocator = (
            TokenBudgetAllocator(settings) if settings is not None else None
        )

    def build_from_retrieval(
        self,
        retrieval: RetrievalResult,
        *,
        context: MemoryContext | None = None,
        conversation_summary: str | None = None,
    ) -> MemoryContext:
        """Apply token budgeting and assemble a canonical ``MemoryContext``."""
        base = context or MemoryContext()
        if conversation_summary is not None:
            base = base.model_copy(
                update={"conversation_summary": conversation_summary}
            )

        ranked = list(retrieval.user_memories) + list(retrieval.project_memories)
        if not ranked:
            metadata = dict(base.metadata)
            metadata.update(retrieval.metadata)
            return base.model_copy(update={"metadata": metadata})

        if self._budget_allocator is None:
            user_memories = [
                record for record in ranked if record.memory_type is MemoryType.USER
            ]
            project_memories = [
                record for record in ranked if record.memory_type is MemoryType.PROJECT
            ]
            char_usage = sum(len(record.content) for record in ranked)
            truncated = False
        else:
            allocation = self._budget_allocator.allocate(ranked)
            user_memories = [
                record
                for record in allocation.records
                if record.memory_type is MemoryType.USER
            ]
            project_memories = [
                record
                for record in allocation.records
                if record.memory_type is MemoryType.PROJECT
            ]
            char_usage = allocation.char_usage
            truncated = allocation.truncated

        metadata = dict(base.metadata)
        metadata.update(retrieval.metadata)
        metadata["memory_char_usage"] = char_usage
        metadata["memory_truncated"] = truncated

        return base.model_copy(
            update={
                "user_memories": user_memories,
                "project_memories": project_memories,
                "metadata": metadata,
                "token_usage": char_usage,
            }
        )

    async def with_preferences(
        self,
        user_id: uuid.UUID,
        *,
        context: MemoryContext | None = None,
    ) -> MemoryContext:
        """Merge normalized user preferences into ``MemoryContext.preferences``."""
        base = context or MemoryContext()
        try:
            raw = await self._provider.list_preferences(user_id=user_id)
            preferences = normalize_preferences(raw)
        except Exception:  # noqa: BLE001 - preferences must not block callers
            logger.warning(
                "User preference retrieval failed",
                user_id=str(user_id),
                exc_info=True,
            )
            return base
        return base.model_copy(update={"preferences": preferences})

    async def with_project_memories(
        self,
        owner_id: uuid.UUID,
        project_id: uuid.UUID,
        *,
        context: MemoryContext | None = None,
    ) -> MemoryContext:
        """Merge normalized project memories into ``MemoryContext.project_memories``."""
        base = context or MemoryContext()
        validated_project_id = validate_project_id(project_id)
        session_id = map_project_id_to_session_id(validated_project_id)
        list_active = getattr(self._provider, "list_active_records", None)
        if list_active is None:
            return base

        try:
            loader = cast(ListActiveRecords, list_active)
            raw = await loader(
                owner_id=owner_id,
                memory_type=MemoryType.PROJECT,
                session_id=session_id,
            )
            project_memories = normalize_project_memories(raw)
        except Exception:  # noqa: BLE001 - project memories must not block callers
            logger.warning(
                "Project memory retrieval failed",
                owner_id=str(owner_id),
                project_id=str(validated_project_id),
                exc_info=True,
            )
            return base
        return base.model_copy(update={"project_memories": project_memories})
