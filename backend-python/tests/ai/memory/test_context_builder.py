"""Tests for MemoryContextBuilder (Phase 4 preferences)."""

from __future__ import annotations

import uuid

import pytest

from app.ai.memory.context_builder import MemoryContextBuilder
from app.ai.memory.models import MemoryContext, MemoryRecord, MemoryScope, MemoryType
from tests.ai.memory.test_manager import FakeMemoryProvider, _NOW


class TestMemoryContextBuilder:
    @pytest.mark.anyio
    async def test_with_preferences_populates_memory_context(self) -> None:
        provider = FakeMemoryProvider()
        user_id = uuid.uuid4()
        await provider.set_preference(
            user_id=user_id,
            key="response_tone",
            value={"tone": "concise"},
        )
        builder = MemoryContextBuilder(provider)  # type: ignore[arg-type]

        context = await builder.with_preferences(user_id)

        assert context.preferences == {"response_tone": {"tone": "concise"}}
        assert context.user_memories == []
        assert context.project_memories == []

    @pytest.mark.anyio
    async def test_with_preferences_preserves_existing_context_fields(self) -> None:
        provider = FakeMemoryProvider()
        user_id = uuid.uuid4()
        await provider.set_preference(
            user_id=user_id, key="preferred_units", value={"system": "metric"}
        )
        base = MemoryContext(conversation_summary="Existing summary.")
        builder = MemoryContextBuilder(provider)  # type: ignore[arg-type]

        context = await builder.with_preferences(user_id, context=base)

        assert context.conversation_summary == "Existing summary."
        assert context.preferences == {"preferred_units": {"system": "metric"}}

    @pytest.mark.anyio
    async def test_with_preferences_keeps_preferences_separate_from_memories(
        self,
    ) -> None:
        provider = FakeMemoryProvider()
        user_id = uuid.uuid4()
        provider.existing_records = [
            MemoryRecord(
                id=uuid.uuid4(),
                memory_type=MemoryType.USER,
                scope=MemoryScope.USER,
                owner_id=user_id,
                content="User prefers dark mode.",
                created_at=_NOW,
                updated_at=_NOW,
                source="api",
            )
        ]
        await provider.set_preference(
            user_id=user_id, key="response_tone", value={"tone": "formal"}
        )
        builder = MemoryContextBuilder(provider)  # type: ignore[arg-type]

        context = await builder.with_preferences(user_id)

        assert context.preferences == {"response_tone": {"tone": "formal"}}
        assert context.user_memories == []

    @pytest.mark.anyio
    async def test_with_preferences_returns_base_context_on_provider_failure(
        self,
    ) -> None:
        provider = FakeMemoryProvider()

        async def failing_list(*args, **kwargs):  # noqa: ANN002, ANN003
            raise RuntimeError("database unavailable")

        provider.list_preferences = failing_list  # type: ignore[method-assign]
        builder = MemoryContextBuilder(provider)  # type: ignore[arg-type]
        base = MemoryContext(conversation_summary="Keep me.")

        context = await builder.with_preferences(uuid.uuid4(), context=base)

        assert context.conversation_summary == "Keep me."
        assert context.preferences == {}
