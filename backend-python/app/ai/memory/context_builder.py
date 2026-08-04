"""Build canonical ``MemoryContext`` from retrieved memory subsystems.

Phase 4 loads structured user preferences. Semantic memories and token
budgeting are added in Phase 6.
"""

from __future__ import annotations

import uuid

from app.ai.memory.interfaces.memory_provider import MemoryProvider
from app.ai.memory.models import MemoryContext
from app.ai.memory.preferences import normalize_preferences
from app.core.logging import get_logger

logger = get_logger(__name__)


class MemoryContextBuilder:
    """Combines retrieved memory inputs into a normalized ``MemoryContext``."""

    def __init__(self, provider: MemoryProvider) -> None:
        self._provider = provider

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
