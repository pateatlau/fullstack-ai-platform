"""Character budget allocation for retrieved memory records (Phase 6).

``memory_token_budget`` caps injected memory block size in characters (Part I
§ Configuration defaults). Internal to the Memory subsystem — not part of the
frozen public API.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.ai.memory.models import MemoryRecord
from app.core.config import Settings


@dataclass(frozen=True)
class BudgetAllocation:
    """Result of applying the memory character budget to ranked records."""

    records: list[MemoryRecord]
    truncated: bool
    char_usage: int


class TokenBudgetAllocator:
    """Allocate the memory block budget across ranked ``MemoryRecord`` rows."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def allocate(
        self,
        records: list[MemoryRecord],
        *,
        max_chars: int | None = None,
    ) -> BudgetAllocation:
        """Include records in rank order until the character budget is exhausted."""
        if not records:
            return BudgetAllocation(records=[], truncated=False, char_usage=0)

        budget = (
            max_chars if max_chars is not None else self._settings.memory_token_budget
        )
        included: list[MemoryRecord] = []
        used = 0

        for record in records:
            content_len = len(record.content)
            if content_len == 0:
                continue
            if used + content_len > budget:
                break
            included.append(record)
            used += content_len

        return BudgetAllocation(
            records=included,
            truncated=len(included) < len(records),
            char_usage=used,
        )
