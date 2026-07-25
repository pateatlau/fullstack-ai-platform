"""Unit tests for metadata filter helpers and pipeline filter wiring (Phase 3)."""

from __future__ import annotations

import uuid
from typing import cast
from unittest.mock import AsyncMock

import pytest

from app.ai.interfaces.vector_store import ScoredChunk
from app.ai.rag.metadata_filter import (
    apply_metadata_filter,
    candidate_matches_filter,
    is_unsatisfiable_filter,
)
from app.ai.rag.pipeline import DefaultAdvancedRetrievalPipeline
from app.ai.rag.retriever import Retriever
from app.ai.rag.schemas import (
    MetadataFilter,
    RetrievalRequest,
    RetrievedCandidate,
)


def _chunk(
    *,
    document_id: uuid.UUID | None = None,
    metadata: dict[str, object] | None = None,
    score: float = 0.9,
) -> ScoredChunk:
    return ScoredChunk(
        chunk_id=uuid.uuid4(),
        document_id=document_id or uuid.uuid4(),
        chunk_index=0,
        content="body",
        metadata=metadata or {},
        score=score,
    )


def _candidate(
    *,
    document_id: uuid.UUID | None = None,
    metadata: dict[str, object] | None = None,
    score: float = 0.9,
) -> RetrievedCandidate:
    chunk = _chunk(document_id=document_id, metadata=metadata, score=score)
    return RetrievedCandidate(
        chunk=chunk,
        parent=None,
        metadata=dict(chunk.metadata),
        final_score=score,
        dense_score=score,
    )


def test_is_unsatisfiable_filter_empty_sets() -> None:
    assert is_unsatisfiable_filter(MetadataFilter()) is False
    assert is_unsatisfiable_filter(MetadataFilter(document_ids=frozenset())) is True
    assert is_unsatisfiable_filter(MetadataFilter(tags=frozenset())) is True
    assert (
        is_unsatisfiable_filter(MetadataFilter(document_ids=frozenset({uuid.uuid4()})))
        is False
    )


def test_candidate_matches_filter_and_semantics() -> None:
    doc_a = uuid.uuid4()
    doc_b = uuid.uuid4()
    candidate = _candidate(
        document_id=doc_a,
        metadata={
            "source": "handbook.pdf",
            "tags": ["policy", "hr"],
            "mime_type": "application/pdf",
        },
    )

    assert candidate_matches_filter(candidate, MetadataFilter()) is True
    assert (
        candidate_matches_filter(
            candidate, MetadataFilter(document_ids=frozenset({doc_a}))
        )
        is True
    )
    assert (
        candidate_matches_filter(
            candidate, MetadataFilter(document_ids=frozenset({doc_b}))
        )
        is False
    )
    assert (
        candidate_matches_filter(
            candidate, MetadataFilter(tags=frozenset({"policy", "hr"}))
        )
        is True
    )
    assert (
        candidate_matches_filter(
            candidate, MetadataFilter(tags=frozenset({"policy", "legal"}))
        )
        is False
    )
    assert (
        candidate_matches_filter(
            candidate,
            MetadataFilter(source="handbook.pdf", mime_type="application/pdf"),
        )
        is True
    )
    assert (
        candidate_matches_filter(candidate, MetadataFilter(source="other.pdf")) is False
    )
    assert (
        candidate_matches_filter(candidate, MetadataFilter(mime_type="text/plain"))
        is False
    )


def test_apply_metadata_filter_preserves_order_and_empty_sets() -> None:
    first = _candidate(metadata={"source": "a.txt", "tags": ["x"]})
    second = _candidate(metadata={"source": "b.txt", "tags": ["x", "y"]})
    third = _candidate(metadata={"source": "c.txt", "tags": ["y"]})

    filtered = apply_metadata_filter(
        [first, second, third],
        MetadataFilter(tags=frozenset({"x"})),
    )
    assert filtered == [first, second]

    assert apply_metadata_filter([first], None) == [first]
    assert apply_metadata_filter([first], MetadataFilter(tags=frozenset())) == []
    assert (
        apply_metadata_filter([first], MetadataFilter(document_ids=frozenset())) == []
    )


@pytest.mark.anyio
async def test_retriever_passes_filters_to_vector_store() -> None:
    from app.core.config import Settings

    user_id = uuid.uuid4()
    query_vector = [0.1, 0.2]
    filters = MetadataFilter(source="notes.md")
    embed = AsyncMock()
    embed.embed_texts = AsyncMock(return_value=[query_vector])
    store = AsyncMock()
    store.similarity_search = AsyncMock(return_value=[])
    retriever = Retriever(
        embedding_provider=embed,
        vector_store=store,
        settings=Settings(openai_api_key="test-key", rag_top_k=5),
    )

    await retriever.retrieve(question="q", user_id=user_id, filters=filters)

    store.similarity_search.assert_awaited_once_with(
        query_vector,
        top_k=5,
        user_id=user_id,
        filters=filters,
    )


@pytest.mark.anyio
async def test_pipeline_pushes_filters_and_applies_candidate_stage() -> None:
    matching = _chunk(
        metadata={"source": "keep.txt", "tags": ["ok"], "mime_type": "text/plain"}
    )
    non_matching = _chunk(
        metadata={"source": "drop.txt", "tags": ["ok"], "mime_type": "text/plain"}
    )

    class _FilteringStubRetriever:
        def __init__(self) -> None:
            self.seen_filters: MetadataFilter | None = None

        async def retrieve(
            self,
            *,
            question: str,
            user_id: uuid.UUID,
            top_k: int | None = None,
            filters: MetadataFilter | None = None,
        ) -> list[ScoredChunk]:
            _ = (question, user_id, top_k)
            self.seen_filters = filters
            # Simulate store push-down already dropping non-matching source.
            if filters is not None and filters.source == "keep.txt":
                return [matching]
            return [matching, non_matching]

    stub = _FilteringStubRetriever()
    pipeline = DefaultAdvancedRetrievalPipeline(retriever=cast(Retriever, stub))
    filters = MetadataFilter(source="keep.txt", tags=frozenset({"ok"}))

    result = await pipeline.retrieve(
        RetrievalRequest(
            question="hello",
            user_id=uuid.uuid4(),
            filters=filters,
        )
    )

    assert stub.seen_filters == filters
    assert len(result.candidates) == 1
    assert result.candidates[0].chunk.content == "body"
    assert result.candidates[0].metadata["source"] == "keep.txt"


@pytest.mark.anyio
async def test_pipeline_unsatisfiable_filter_returns_empty_not_error() -> None:
    class _ExplodingRetriever:
        async def retrieve(self, **_: object) -> list[ScoredChunk]:
            raise AssertionError("retriever should not be called")

    pipeline = DefaultAdvancedRetrievalPipeline(
        retriever=cast(Retriever, _ExplodingRetriever())
    )
    result = await pipeline.retrieve(
        RetrievalRequest(
            question="hello",
            user_id=uuid.uuid4(),
            filters=MetadataFilter(document_ids=frozenset()),
        )
    )
    assert result.candidates == ()
    assert result.retrieval_latency_ms is not None
