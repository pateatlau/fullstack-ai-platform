"""Tests for MemoryQualityEvaluator (Epic 05 Phase 3)."""

from __future__ import annotations

import datetime
import uuid

from app.ai.memory.extraction import CandidateMemory
from app.ai.memory.lifecycle import LifecycleState
from app.ai.memory.models import MemoryRecord, MemoryScope, MemoryType
from app.ai.memory.quality import MemoryQualityEvaluator, cosine_similarity
from app.core.config import Settings

_NOW = datetime.datetime.now(datetime.timezone.utc)


def _candidate(**overrides: object) -> CandidateMemory:
    defaults: dict[str, object] = {
        "memory_type": MemoryType.USER,
        "content": "User prefers concise answers.",
        "confidence": 0.9,
        "importance": 0.8,
    }
    defaults.update(overrides)
    return CandidateMemory(**defaults)  # type: ignore[arg-type]


def _record(content: str, embedding: list[float]) -> MemoryRecord:
    return MemoryRecord(
        id=uuid.uuid4(),
        memory_type=MemoryType.USER,
        scope=MemoryScope.USER,
        owner_id=uuid.uuid4(),
        content=content,
        embedding=embedding,
        created_at=_NOW,
        updated_at=_NOW,
        lifecycle_state=LifecycleState.ACTIVE,
        source="api",
    )


def _evaluator() -> MemoryQualityEvaluator:
    return MemoryQualityEvaluator(
        Settings(
            openai_api_key="test-key",
            memory_min_confidence=0.5,
            memory_min_quality_score=0.4,
            memory_dedupe_similarity_threshold=0.92,
        )
    )


class TestCosineSimilarity:
    def test_identical_vectors_score_one(self) -> None:
        vector = [1.0, 0.0, 0.0]
        assert cosine_similarity(vector, vector) == 1.0


class TestMemoryQualityEvaluator:
    def test_rejects_low_confidence(self) -> None:
        evaluator = _evaluator()
        approved = evaluator.filter_preliminary([_candidate(confidence=0.2)])
        assert approved == []

    def test_rejects_ephemeral_phrasing(self) -> None:
        evaluator = _evaluator()
        approved = evaluator.filter_preliminary(
            [_candidate(content="In this conversation we are discussing APIs.")]
        )
        assert approved == []

    def test_rejects_duplicate_content_in_batch(self) -> None:
        evaluator = _evaluator()
        approved = evaluator.filter_preliminary(
            [
                _candidate(content="User prefers TypeScript."),
                _candidate(content="user prefers typescript."),
            ]
        )
        assert len(approved) == 1

    def test_dedupe_rejects_near_duplicate_embeddings(self) -> None:
        evaluator = _evaluator()
        left = [1.0, 0.0, 0.0]
        right = [0.999, 0.001, 0.0]
        approved = evaluator.dedupe_by_embedding(
            [_candidate(), _candidate(content="Another fact about TypeScript.")],
            [left, right],
            [],
        )
        assert len(approved) == 1

    def test_dedupe_rejects_match_against_existing_record(self) -> None:
        evaluator = _evaluator()
        embedding = [1.0, 0.0, 0.0]
        existing = [_record("Stored fact", embedding)]
        approved = evaluator.dedupe_by_embedding(
            [_candidate(content="Stored fact variant.")],
            [embedding],
            existing,
        )
        assert approved == []
