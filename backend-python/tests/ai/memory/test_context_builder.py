"""Tests for MemoryContextBuilder (Phase 4 preferences, Phase 6 retrieval)."""

from __future__ import annotations

import uuid

import pytest

from app.ai.memory.context_builder import MemoryContextBuilder
from app.ai.memory.models import MemoryContext, MemoryRecord, MemoryScope, MemoryType
from app.ai.memory.semantic_retriever import RetrievalResult
from app.core.config import Settings
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

    @pytest.mark.anyio
    async def test_with_project_memories_populates_memory_context(self) -> None:
        provider = FakeMemoryProvider()
        owner_id = uuid.uuid4()
        project_id = uuid.uuid4()
        provider.existing_records = [
            MemoryRecord(
                id=uuid.uuid4(),
                memory_type=MemoryType.PROJECT,
                scope=MemoryScope.PROJECT,
                owner_id=owner_id,
                project_id=project_id,
                content="Uses FastAPI.",
                created_at=_NOW,
                updated_at=_NOW,
                source="api",
            )
        ]
        builder = MemoryContextBuilder(provider)  # type: ignore[arg-type]

        context = await builder.with_project_memories(owner_id, project_id)

        assert len(context.project_memories) == 1
        assert context.project_memories[0].content == "Uses FastAPI."
        assert context.preferences == {}

    @pytest.mark.anyio
    async def test_with_project_memories_preserves_existing_context_fields(
        self,
    ) -> None:
        provider = FakeMemoryProvider()
        owner_id = uuid.uuid4()
        project_id = uuid.uuid4()
        provider.existing_records = [
            MemoryRecord(
                id=uuid.uuid4(),
                memory_type=MemoryType.PROJECT,
                scope=MemoryScope.PROJECT,
                owner_id=owner_id,
                project_id=project_id,
                content="Scoped fact.",
                created_at=_NOW,
                updated_at=_NOW,
                source="api",
            )
        ]
        base = MemoryContext(preferences={"response_tone": {"tone": "formal"}})
        builder = MemoryContextBuilder(provider)  # type: ignore[arg-type]

        context = await builder.with_project_memories(
            owner_id, project_id, context=base
        )

        assert context.preferences == {"response_tone": {"tone": "formal"}}
        assert len(context.project_memories) == 1

    @pytest.mark.anyio
    async def test_with_project_memories_keeps_project_memories_separate_from_preferences(
        self,
    ) -> None:
        provider = FakeMemoryProvider()
        owner_id = uuid.uuid4()
        project_id = uuid.uuid4()
        await provider.set_preference(
            user_id=owner_id, key="response_tone", value={"tone": "formal"}
        )
        provider.existing_records = [
            MemoryRecord(
                id=uuid.uuid4(),
                memory_type=MemoryType.PROJECT,
                scope=MemoryScope.PROJECT,
                owner_id=owner_id,
                project_id=project_id,
                content="Project-only.",
                created_at=_NOW,
                updated_at=_NOW,
                source="api",
            )
        ]
        builder = MemoryContextBuilder(provider)  # type: ignore[arg-type]

        context = await builder.with_project_memories(owner_id, project_id)

        assert context.preferences == {}
        assert len(context.project_memories) == 1

    @pytest.mark.anyio
    async def test_with_project_memories_returns_base_context_on_provider_failure(
        self,
    ) -> None:
        provider = FakeMemoryProvider()

        async def failing_list(*args, **kwargs):  # noqa: ANN002, ANN003
            raise RuntimeError("database unavailable")

        provider.list_active_records = failing_list  # type: ignore[method-assign]
        builder = MemoryContextBuilder(provider)  # type: ignore[arg-type]
        base = MemoryContext(conversation_summary="Keep me.")

        context = await builder.with_project_memories(
            uuid.uuid4(), uuid.uuid4(), context=base
        )

        assert context.conversation_summary == "Keep me."
        assert context.project_memories == []


class TestMemoryContextBuilderRetrieval:
    def test_build_from_retrieval_applies_token_budget(self) -> None:
        owner_id = uuid.uuid4()
        user_record = MemoryRecord(
            id=uuid.uuid4(),
            memory_type=MemoryType.USER,
            scope=MemoryScope.USER,
            owner_id=owner_id,
            content="A" * 100,
            created_at=_NOW,
            updated_at=_NOW,
            source="test",
        )
        project_record = MemoryRecord(
            id=uuid.uuid4(),
            memory_type=MemoryType.PROJECT,
            scope=MemoryScope.PROJECT,
            owner_id=owner_id,
            project_id=uuid.uuid4(),
            content="B" * 100,
            created_at=_NOW,
            updated_at=_NOW,
            source="test",
        )
        retrieval = RetrievalResult(
            user_memories=[user_record],
            project_memories=[project_record],
            metadata={"memories_ranked": 2},
        )
        builder = MemoryContextBuilder(
            FakeMemoryProvider(),  # type: ignore[arg-type]
            settings=Settings(openai_api_key="test-key", memory_token_budget=150),
        )

        context = builder.build_from_retrieval(retrieval)

        assert len(context.user_memories) == 1
        assert context.project_memories == []
        assert context.metadata["memory_truncated"] is True
        assert context.token_usage == 100

    def test_build_from_retrieval_preserves_conversation_summary(self) -> None:
        retrieval = RetrievalResult(
            user_memories=[],
            project_memories=[],
            metadata={},
        )
        builder = MemoryContextBuilder(
            FakeMemoryProvider(),  # type: ignore[arg-type]
            settings=Settings(openai_api_key="test-key"),
        )

        context = builder.build_from_retrieval(
            retrieval,
            conversation_summary="Earlier discussion.",
        )

        assert context.conversation_summary == "Earlier discussion."

    @pytest.mark.anyio
    async def test_build_from_retrieval_keeps_preferences_separate(self) -> None:
        provider = FakeMemoryProvider()
        owner_id = uuid.uuid4()
        await provider.set_preference(
            user_id=owner_id,
            key="response_tone",
            value={"tone": "formal"},
        )
        user_record = MemoryRecord(
            id=uuid.uuid4(),
            memory_type=MemoryType.USER,
            scope=MemoryScope.USER,
            owner_id=owner_id,
            content="Semantic fact.",
            created_at=_NOW,
            updated_at=_NOW,
            source="test",
        )
        retrieval = RetrievalResult(
            user_memories=[user_record],
            project_memories=[],
            metadata={},
        )
        builder = MemoryContextBuilder(
            provider,  # type: ignore[arg-type]
            settings=Settings(openai_api_key="test-key"),
        )

        context = builder.build_from_retrieval(retrieval)
        context = await builder.with_preferences(owner_id, context=context)

        assert len(context.user_memories) == 1
        assert context.preferences == {"response_tone": {"tone": "formal"}}
