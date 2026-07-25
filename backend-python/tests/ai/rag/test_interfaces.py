"""Tests for advanced RAG Protocol interfaces and pipeline skeleton (Phase 1)."""

from __future__ import annotations

import uuid
from typing import cast

import pytest

from app.ai.interfaces import (
    ContextCompressor,
    IndexingJob,
    QueryRewriter,
    Reranker,
)
from app.ai.interfaces.vector_store import ScoredChunk
from app.ai.rag import (
    AdvancedRetrievalPipeline,
    BuiltContext,
    DefaultAdvancedRetrievalPipeline,
    IndexingJobState,
    IndexingJobStatus,
    MetadataFilter,
    RetrievalRequest,
    RetrievalResult,
    RetrievedCandidate,
)
from app.ai.rag.retriever import Retriever


class _StubQueryRewriter:
    async def rewrite(self, query: str, *, user_id: uuid.UUID) -> str:
        _ = user_id
        return f"rewritten:{query}"


class _StubReranker:
    async def rerank(
        self,
        query: str,
        candidates: list[RetrievedCandidate],
        *,
        top_n: int,
    ) -> list[RetrievedCandidate]:
        _ = query
        return list(candidates[:top_n])


class _StubContextCompressor:
    def compress(
        self,
        candidates: list[RetrievedCandidate],
        *,
        max_chars: int,
    ) -> BuiltContext:
        _ = max_chars
        chunks = [candidate.chunk for candidate in candidates]
        return BuiltContext(text="ctx", included_chunks=chunks, truncated=False)


class _StubIndexingJob:
    async def submit(self, *, document_id: uuid.UUID, user_id: uuid.UUID) -> str:
        _ = (document_id, user_id)
        return "job-stub"

    async def get_status(self, job_id: str) -> IndexingJobStatus:
        return IndexingJobStatus(job_id=job_id, state=IndexingJobState.SUCCEEDED)


class _StubRetriever:
    def __init__(self, chunks: list[ScoredChunk]) -> None:
        self._chunks = chunks

    async def retrieve(
        self,
        *,
        question: str,
        user_id: uuid.UUID,
        top_k: int | None = None,
        filters: MetadataFilter | None = None,
    ) -> list[ScoredChunk]:
        _ = (question, user_id, top_k, filters)
        return list(self._chunks)


def test_public_api_exports_are_present() -> None:
    import app.ai.interfaces as interfaces_pkg
    import app.ai.rag as rag_pkg

    for name in (
        "AdvancedRetrievalPipeline",
        "DefaultAdvancedRetrievalPipeline",
        "RetrievalRequest",
        "RetrievalResult",
        "RetrievedCandidate",
        "Citation",
        "MetadataFilter",
        "IndexingJobState",
        "IndexingJobStatus",
    ):
        assert hasattr(rag_pkg, name), f"missing rag export: {name}"

    for name in ("QueryRewriter", "Reranker", "ContextCompressor", "IndexingJob"):
        assert hasattr(interfaces_pkg, name), f"missing interfaces export: {name}"


def test_stub_types_satisfy_protocols() -> None:
    rewriter: QueryRewriter = _StubQueryRewriter()
    reranker: Reranker = _StubReranker()
    compressor: ContextCompressor = _StubContextCompressor()
    indexing: IndexingJob = _StubIndexingJob()
    pipeline: AdvancedRetrievalPipeline = DefaultAdvancedRetrievalPipeline(
        retriever=cast(Retriever, _StubRetriever([]))
    )

    assert rewriter is not None
    assert reranker is not None
    assert compressor is not None
    assert indexing is not None
    assert pipeline is not None


@pytest.mark.anyio
async def test_stub_protocols_round_trip() -> None:
    user_id = uuid.uuid4()
    chunk = ScoredChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=0,
        content="body",
        metadata={},
        score=0.7,
    )
    candidate = RetrievedCandidate(
        chunk=chunk,
        parent=None,
        metadata={},
        final_score=0.7,
        dense_score=0.7,
    )

    rewriter = _StubQueryRewriter()
    assert await rewriter.rewrite("q", user_id=user_id) == "rewritten:q"

    reranker = _StubReranker()
    reranked = await reranker.rerank("q", [candidate], top_n=1)
    assert len(reranked) == 1

    compressor = _StubContextCompressor()
    built = compressor.compress([candidate], max_chars=100)
    assert built.text == "ctx"
    assert built.included_chunks == [chunk]

    indexing = _StubIndexingJob()
    job_id = await indexing.submit(document_id=chunk.document_id, user_id=user_id)
    status = await indexing.get_status(job_id)
    assert status.state is IndexingJobState.SUCCEEDED


@pytest.mark.anyio
async def test_default_pipeline_delegates_to_retriever() -> None:
    chunk = ScoredChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=1,
        content="delegated",
        metadata={"source": "a.txt"},
        score=0.88,
    )
    pipeline: AdvancedRetrievalPipeline = DefaultAdvancedRetrievalPipeline(
        retriever=cast(Retriever, _StubRetriever([chunk]))
    )

    result = await pipeline.retrieve(
        RetrievalRequest(question="hello", user_id=uuid.uuid4(), top_k=3)
    )

    assert isinstance(result, RetrievalResult)
    assert len(result.candidates) == 1
    assert result.candidates[0].chunk.content == "delegated"
    assert result.candidates[0].final_score == 0.88
    assert result.candidates[0].dense_score == 0.88
    assert result.candidates[0].parent is None
    assert result.citations == ()
    assert result.context_text == ""
    assert result.retrieval_latency_ms is not None
    assert result.retrieval_latency_ms >= 0


@pytest.mark.anyio
async def test_default_pipeline_accepts_filters() -> None:
    chunk = ScoredChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=0,
        content="filtered",
        metadata={"source": "notes.md"},
        score=0.5,
    )
    pipeline = DefaultAdvancedRetrievalPipeline(
        retriever=cast(Retriever, _StubRetriever([chunk]))
    )
    result = await pipeline.retrieve(
        RetrievalRequest(
            question="hello",
            user_id=uuid.uuid4(),
            filters=MetadataFilter(source="notes.md"),
        )
    )
    assert len(result.candidates) == 1
    assert result.candidates[0].chunk.content == "filtered"
