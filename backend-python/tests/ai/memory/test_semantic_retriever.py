"""Tests for SemanticRetriever (Phase 6)."""

from __future__ import annotations

import datetime
import time
import uuid

import pytest

from app.ai.memory.lifecycle import LifecycleState
from app.ai.memory.models import MemoryRecord, MemoryScope, MemoryType
from app.ai.memory.semantic_retriever import (
    SemanticRetriever,
    build_retrieval_query,
    compute_rank_score,
    normalize_retrieval_query,
    rank_scored_memories,
)
from app.ai.memory.semantic_retriever import ScoredMemory
from app.core.config import Settings
from app.schemas.chat import ChatMessageSchema
from tests.ai.memory.test_manager import FakeMemoryProvider

_NOW = datetime.datetime.now(datetime.timezone.utc)
DIMENSIONS = 8


def _vector(primary: float, secondary: float = 0.0) -> list[float]:
    vec = [0.0] * DIMENSIONS
    vec[0] = primary
    vec[1] = secondary
    return vec


def _record(
    *,
    owner_id: uuid.UUID,
    content: str,
    embedding: list[float],
    memory_type: MemoryType = MemoryType.USER,
    project_id: uuid.UUID | None = None,
    lifecycle_state: LifecycleState = LifecycleState.ACTIVE,
    confidence: float = 0.8,
    quality_score: float = 0.8,
    importance: float = 0.7,
    record_id: uuid.UUID | None = None,
) -> MemoryRecord:
    return MemoryRecord(
        id=record_id or uuid.uuid4(),
        memory_type=memory_type,
        scope=MemoryScope.PROJECT
        if memory_type is MemoryType.PROJECT
        else MemoryScope.USER,
        owner_id=owner_id,
        project_id=project_id,
        content=content,
        embedding=embedding,
        confidence=confidence,
        quality_score=quality_score,
        importance=importance,
        created_at=_NOW,
        updated_at=_NOW,
        lifecycle_state=lifecycle_state,
        source="test",
    )


class _FakeEmbeddingProvider:
    dimensions = DIMENSIONS

    def __init__(
        self, vector: list[float] | None = None, *, fail: bool = False
    ) -> None:
        self._vector = vector or _vector(1.0)
        self._fail = fail
        self.calls: list[list[str]] = []

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        if self._fail:
            raise RuntimeError("embedding unavailable")
        return [self._vector]


class _AlwaysOwnsSessionChecker:
    async def user_owns_session(
        self, *, user_id: uuid.UUID, session_id: uuid.UUID
    ) -> bool:
        del user_id, session_id
        return True


class _NeverOwnsSessionChecker:
    async def user_owns_session(
        self, *, user_id: uuid.UUID, session_id: uuid.UUID
    ) -> bool:
        del user_id, session_id
        return False


def _settings(**overrides: object) -> Settings:
    base = {
        "openai_api_key": "test-key",
        "memory_enabled": True,
        "embedding_dimensions": DIMENSIONS,
        "memory_retrieval_top_k": 8,
        "memory_min_confidence": 0.5,
        "memory_min_quality_score": 0.4,
        "memory_dedupe_similarity_threshold": 0.92,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _retriever(
    provider: FakeMemoryProvider,
    *,
    embedding: _FakeEmbeddingProvider | None = None,
    session_checker: object | None = None,
    settings: Settings | None = None,
) -> SemanticRetriever:
    return SemanticRetriever(
        provider,  # type: ignore[arg-type]
        embedding or _FakeEmbeddingProvider(),
        settings or _settings(),
        session_ownership_checker=session_checker,  # type: ignore[arg-type]
    )


class TestQueryPreparation:
    def test_build_retrieval_query_includes_summary_and_messages(self) -> None:
        messages = [
            ChatMessageSchema(role="user", content="What is FastAPI?"),
            ChatMessageSchema(role="assistant", content="A Python web framework."),
        ]

        query = build_retrieval_query(messages, "Previous discussion about APIs.")

        assert "Previous discussion about APIs." in query
        assert "What is FastAPI?" in query
        assert "A Python web framework." in query

    def test_build_retrieval_query_ignores_system_messages(self) -> None:
        messages = [
            ChatMessageSchema(role="system", content="You are helpful."),
            ChatMessageSchema(role="user", content="Hello"),
        ]

        query = build_retrieval_query(messages)

        assert "You are helpful." not in query
        assert "Hello" in query

    def test_normalize_retrieval_query_collapses_whitespace(self) -> None:
        assert normalize_retrieval_query("  hello   world\n\n") == "hello world"


class TestRanking:
    def test_compute_rank_score_weights_similarity_and_quality(self) -> None:
        record = _record(
            owner_id=uuid.uuid4(),
            content="test",
            embedding=_vector(1.0),
            confidence=1.0,
            quality_score=1.0,
            importance=1.0,
        )

        high = compute_rank_score(record, similarity=1.0)
        low = compute_rank_score(record, similarity=0.0)

        assert high > low

    def test_rank_scored_memories_is_deterministic_for_equal_scores(self) -> None:
        owner_id = uuid.uuid4()
        first_id = uuid.UUID("00000000-0000-4000-8000-000000000001")
        second_id = uuid.UUID("00000000-0000-4000-8000-000000000002")
        scored = [
            ScoredMemory(
                record=_record(
                    owner_id=owner_id,
                    content="b",
                    embedding=_vector(1.0),
                    record_id=second_id,
                ),
                similarity=0.9,
                rank_score=0.9,
            ),
            ScoredMemory(
                record=_record(
                    owner_id=owner_id,
                    content="a",
                    embedding=_vector(1.0),
                    record_id=first_id,
                ),
                similarity=0.9,
                rank_score=0.9,
            ),
        ]

        ranked = rank_scored_memories(scored)

        assert ranked[0].record.id == first_id
        assert ranked[1].record.id == second_id


class TestSemanticRetriever:
    @pytest.mark.anyio
    async def test_retrieve_returns_user_and_project_memories(self) -> None:
        owner_id = uuid.uuid4()
        project_id = uuid.uuid4()
        provider = FakeMemoryProvider()
        provider.existing_records = [
            _record(
                owner_id=owner_id,
                content="User prefers Python.",
                embedding=_vector(1.0),
            ),
            _record(
                owner_id=owner_id,
                content="Project uses FastAPI.",
                embedding=_vector(0.95, 0.05),
                memory_type=MemoryType.PROJECT,
                project_id=project_id,
            ),
        ]
        retriever = _retriever(
            provider,
            session_checker=_AlwaysOwnsSessionChecker(),
        )
        messages = [ChatMessageSchema(role="user", content="Tell me about the stack.")]

        result = await retriever.retrieve(
            owner_id=owner_id,
            messages=messages,
            project_id=project_id,
        )

        assert len(result.user_memories) == 1
        assert len(result.project_memories) == 1
        assert "retrieval_latency_ms" in result.metadata

    @pytest.mark.anyio
    async def test_retrieve_excludes_archived_and_deleted_memories(self) -> None:
        owner_id = uuid.uuid4()
        provider = FakeMemoryProvider()
        provider.existing_records = [
            _record(
                owner_id=owner_id,
                content="Active memory.",
                embedding=_vector(1.0),
                lifecycle_state=LifecycleState.ACTIVE,
            ),
            _record(
                owner_id=owner_id,
                content="Archived memory.",
                embedding=_vector(1.0),
                lifecycle_state=LifecycleState.ARCHIVED,
            ),
            _record(
                owner_id=owner_id,
                content="Deleted memory.",
                embedding=_vector(1.0),
                lifecycle_state=LifecycleState.DELETED,
            ),
        ]
        retriever = _retriever(provider)
        messages = [ChatMessageSchema(role="user", content="Recall facts.")]

        result = await retriever.retrieve(owner_id=owner_id, messages=messages)

        assert len(result.user_memories) == 1
        assert result.user_memories[0].content == "Active memory."

    @pytest.mark.anyio
    async def test_retrieve_excludes_low_quality_memories(self) -> None:
        owner_id = uuid.uuid4()
        provider = FakeMemoryProvider()
        provider.existing_records = [
            _record(
                owner_id=owner_id,
                content="Good memory.",
                embedding=_vector(1.0),
                quality_score=0.8,
                confidence=0.8,
            ),
            _record(
                owner_id=owner_id,
                content="Low quality.",
                embedding=_vector(1.0),
                quality_score=0.2,
                confidence=0.8,
            ),
        ]
        retriever = _retriever(provider)
        messages = [ChatMessageSchema(role="user", content="Recall facts.")]

        result = await retriever.retrieve(owner_id=owner_id, messages=messages)

        assert len(result.user_memories) == 1
        assert result.user_memories[0].content == "Good memory."

    @pytest.mark.anyio
    async def test_retrieve_deduplicates_semantically_similar_memories(self) -> None:
        owner_id = uuid.uuid4()
        provider = FakeMemoryProvider()
        provider.existing_records = [
            _record(
                owner_id=owner_id,
                content="Primary fact.",
                embedding=_vector(1.0, 0.0),
                quality_score=0.9,
            ),
            _record(
                owner_id=owner_id,
                content="Near duplicate.",
                embedding=_vector(0.99, 0.01),
                quality_score=0.5,
            ),
        ]
        retriever = _retriever(provider)
        messages = [ChatMessageSchema(role="user", content="Recall facts.")]

        result = await retriever.retrieve(owner_id=owner_id, messages=messages)

        assert len(result.user_memories) == 1
        assert result.user_memories[0].content == "Primary fact."

    @pytest.mark.anyio
    async def test_retrieve_skips_project_domain_when_session_not_owned(self) -> None:
        owner_id = uuid.uuid4()
        project_id = uuid.uuid4()
        provider = FakeMemoryProvider()
        provider.existing_records = [
            _record(
                owner_id=owner_id,
                content="Project fact.",
                embedding=_vector(1.0),
                memory_type=MemoryType.PROJECT,
                project_id=project_id,
            ),
        ]
        retriever = _retriever(
            provider,
            session_checker=_NeverOwnsSessionChecker(),
        )
        messages = [ChatMessageSchema(role="user", content="Project info.")]

        result = await retriever.retrieve(
            owner_id=owner_id,
            messages=messages,
            project_id=project_id,
        )

        assert result.project_memories == []
        assert "project_id" not in result.metadata

    @pytest.mark.anyio
    async def test_retrieve_skips_invalid_project_id_but_returns_user_memories(
        self,
    ) -> None:
        owner_id = uuid.uuid4()
        provider = FakeMemoryProvider()
        provider.existing_records = [
            _record(
                owner_id=owner_id,
                content="User fact.",
                embedding=_vector(1.0),
            ),
        ]
        retriever = _retriever(provider)
        messages = [ChatMessageSchema(role="user", content="Recall facts.")]

        result = await retriever.retrieve(
            owner_id=owner_id,
            messages=messages,
            project_id=uuid.UUID(int=0),
        )

        assert len(result.user_memories) == 1
        assert result.project_memories == []
        assert "project_id" not in result.metadata

    @pytest.mark.anyio
    async def test_retrieve_handles_embedding_failure_gracefully(self) -> None:
        owner_id = uuid.uuid4()
        provider = FakeMemoryProvider()
        provider.existing_records = [
            _record(
                owner_id=owner_id,
                content="Should not appear.",
                embedding=_vector(1.0),
            ),
        ]
        retriever = _retriever(
            provider,
            embedding=_FakeEmbeddingProvider(fail=True),
        )
        messages = [ChatMessageSchema(role="user", content="Hello")]

        result = await retriever.retrieve(owner_id=owner_id, messages=messages)

        assert result.user_memories == []
        assert result.metadata.get("retrieval_skipped") == "embedding_failed"

    @pytest.mark.anyio
    async def test_retrieve_returns_empty_for_empty_query(self) -> None:
        provider = FakeMemoryProvider()
        retriever = _retriever(provider)

        result = await retriever.retrieve(
            owner_id=uuid.uuid4(),
            messages=[],
        )

        assert result.user_memories == []
        assert result.metadata.get("retrieval_skipped") == "empty_query"

    @pytest.mark.anyio
    async def test_retrieve_benchmark_completes_quickly_with_many_records(self) -> None:
        owner_id = uuid.uuid4()
        provider = FakeMemoryProvider()
        provider.existing_records = [
            _record(
                owner_id=owner_id,
                content=f"Memory {index}.",
                embedding=_vector(1.0 - index * 0.001),
            )
            for index in range(50)
        ]
        retriever = _retriever(provider)
        messages = [ChatMessageSchema(role="user", content="Benchmark query.")]

        started = time.perf_counter()
        result = await retriever.retrieve(owner_id=owner_id, messages=messages)
        elapsed_ms = (time.perf_counter() - started) * 1000

        assert len(result.user_memories) > 0
        assert elapsed_ms < 500
