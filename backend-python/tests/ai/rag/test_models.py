"""Tests for advanced RAG models (Phase 1)."""

from __future__ import annotations

import uuid
from dataclasses import FrozenInstanceError, replace

import pytest

from app.ai.interfaces.vector_store import ScoredChunk
from app.ai.rag import (
    Citation,
    IndexingJobState,
    IndexingJobStatus,
    MetadataFilter,
    RetrievalRequest,
    RetrievalResult,
    RetrievedCandidate,
)


def _scored_chunk(*, score: float = 0.9) -> ScoredChunk:
    return ScoredChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=0,
        content="chunk body",
        metadata={"source": "doc.txt", "tags": ["a"]},
        score=score,
    )


def test_retrieved_candidate_is_immutable_and_documents_score_semantics() -> None:
    chunk = _scored_chunk(score=0.42)
    candidate = RetrievedCandidate(
        chunk=chunk,
        parent=None,
        metadata=dict(chunk.metadata),
        final_score=0.8,
        dense_score=0.42,
        lexical_score=0.1,
        rrf_score=0.8,
        rerank_score=None,
    )

    with pytest.raises(FrozenInstanceError):
        candidate.final_score = 1.0  # type: ignore[misc]

    updated = replace(candidate, final_score=0.95, rerank_score=0.95)
    assert updated.final_score == 0.95
    assert updated.rerank_score == 0.95
    assert candidate.final_score == 0.8
    # Diagnostic scores must not be required for construction.
    minimal = RetrievedCandidate(
        chunk=chunk,
        parent=None,
        metadata={},
        final_score=0.5,
    )
    assert minimal.dense_score is None
    assert minimal.lexical_score is None
    assert minimal.rrf_score is None
    assert minimal.rerank_score is None


def test_metadata_filter_defaults_and_frozenset_fields() -> None:
    empty = MetadataFilter()
    assert empty.document_ids is None
    assert empty.tags is None
    assert empty.source is None
    assert empty.mime_type is None

    doc_id = uuid.uuid4()
    filt = MetadataFilter(
        document_ids=frozenset({doc_id}),
        tags=frozenset({"policy"}),
        source="handbook.pdf",
        mime_type="application/pdf",
    )
    assert doc_id in filt.document_ids  # type: ignore[operator]
    assert "policy" in filt.tags  # type: ignore[operator]


def test_retrieval_request_and_result_defaults() -> None:
    user_id = uuid.uuid4()
    request = RetrievalRequest(question="What is RAG?", user_id=user_id)
    assert request.top_k is None
    assert request.filters is None

    result = RetrievalResult(candidates=[])
    assert result.citations == []
    assert result.context_text == ""
    assert result.truncated is False
    assert result.retrieval_latency_ms is None


def test_citation_fields_match_part_i() -> None:
    citation = Citation(
        index=1,
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        snippet="original excerpt",
        score=0.91,
        filename="notes.md",
        source="notes.md",
        page=3,
    )
    assert citation.index == 1
    assert citation.snippet == "original excerpt"
    assert citation.page == 3


def test_indexing_job_state_values_are_stable() -> None:
    assert {member.value for member in IndexingJobState} == {
        "queued",
        "running",
        "succeeded",
        "failed",
    }
    status = IndexingJobStatus(job_id="job-1", state=IndexingJobState.QUEUED)
    assert status.error_message is None
