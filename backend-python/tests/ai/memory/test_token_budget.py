"""Tests for TokenBudgetAllocator (Phase 6)."""

from __future__ import annotations

import datetime
import uuid

from app.ai.memory.models import MemoryRecord, MemoryScope, MemoryType
from app.ai.memory.token_budget import TokenBudgetAllocator
from app.core.config import Settings

_NOW = datetime.datetime.now(datetime.timezone.utc)


def _record(content: str) -> MemoryRecord:
    return MemoryRecord(
        id=uuid.uuid4(),
        memory_type=MemoryType.USER,
        scope=MemoryScope.USER,
        owner_id=uuid.uuid4(),
        content=content,
        created_at=_NOW,
        updated_at=_NOW,
        source="test",
    )


class TestTokenBudgetAllocator:
    def test_allocate_includes_records_until_budget_exhausted(self) -> None:
        allocator = TokenBudgetAllocator(
            Settings(openai_api_key="test-key", memory_token_budget=10)
        )
        records = [_record("12345"), _record("67890"), _record("abc")]

        result = allocator.allocate(records)

        assert [record.content for record in result.records] == ["12345", "67890"]
        assert result.truncated is True
        assert result.char_usage == 10

    def test_allocate_returns_all_when_under_budget(self) -> None:
        allocator = TokenBudgetAllocator(
            Settings(openai_api_key="test-key", memory_token_budget=100)
        )
        records = [_record("short"), _record("also short")]

        result = allocator.allocate(records)

        assert len(result.records) == 2
        assert result.truncated is False

    def test_allocate_empty_list(self) -> None:
        allocator = TokenBudgetAllocator(Settings(openai_api_key="test-key"))

        result = allocator.allocate([])

        assert result.records == []
        assert result.truncated is False
        assert result.char_usage == 0

    def test_allocate_respects_custom_max_chars(self) -> None:
        allocator = TokenBudgetAllocator(
            Settings(openai_api_key="test-key", memory_token_budget=1000)
        )
        records = [_record("0123456789")]

        result = allocator.allocate(records, max_chars=5)

        assert result.records == []
        assert result.truncated is True
