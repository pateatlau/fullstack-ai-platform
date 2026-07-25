"""Tests for Reranker Protocol wiring in the advanced pipeline (Epic 02 Phase 6)."""

from __future__ import annotations

import logging
import uuid
from dataclasses import replace
from typing import cast

import pytest

from app.ai.interfaces.vector_store import ScoredChunk
from app.ai.rag.pipeline import DefaultAdvancedRetrievalPipeline
from app.ai.rag.retriever import Retriever
from app.ai.rag.schemas import RetrievalRequest, RetrievedCandidate
from app.core.config import Settings


def _settings(*, advanced_rag_enabled: bool = True) -> Settings:
    return Settings(
        openai_api_key="test-key",
        advanced_rag_enabled=advanced_rag_enabled,
        rag_top_k=5,
    )


def _chunk(*, content: str, score: float) -> ScoredChunk:
    return ScoredChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=0,
        content=content,
        metadata={"source": "doc.txt"},
        score=score,
    )


def _candidate(*, content: str, final_score: float) -> RetrievedCandidate:
    chunk = _chunk(content=content, score=final_score)
    return RetrievedCandidate(
        chunk=chunk,
        parent=None,
        metadata=dict(chunk.metadata),
        final_score=final_score,
        dense_score=final_score,
    )


class _CapturingRetriever:
    def __init__(self, chunks: list[ScoredChunk]) -> None:
        self._chunks = chunks

    async def retrieve(
        self,
        *,
        question: str,
        user_id: uuid.UUID,
        top_k: int | None = None,
        filters: object | None = None,
    ) -> list[ScoredChunk]:
        _ = (question, user_id, top_k, filters)
        return list(self._chunks)


class FakeReranker:
    """Deterministic Protocol-compliant reranker for unit tests."""

    def __init__(
        self,
        *,
        reverse: bool = True,
        error: Exception | None = None,
    ) -> None:
        self.reverse = reverse
        self.error = error
        self.calls: list[tuple[str, int, int]] = []

    async def rerank(
        self,
        query: str,
        candidates: list[RetrievedCandidate],
        *,
        top_n: int,
    ) -> list[RetrievedCandidate]:
        self.calls.append((query, len(candidates), top_n))
        if self.error is not None:
            raise self.error
        ordered = list(reversed(candidates)) if self.reverse else list(candidates)
        trimmed = ordered[:top_n]
        return [
            replace(
                candidate,
                rerank_score=1.0 - (index * 0.1),
                final_score=1.0 - (index * 0.1),
            )
            for index, candidate in enumerate(trimmed)
        ]


@pytest.mark.anyio
async def test_pipeline_reranks_when_advanced_flag_on() -> None:
    chunks = [
        _chunk(content="first", score=0.9),
        _chunk(content="second", score=0.8),
        _chunk(content="third", score=0.7),
    ]
    reranker = FakeReranker(reverse=True)
    pipeline = DefaultAdvancedRetrievalPipeline(
        retriever=cast(Retriever, _CapturingRetriever(chunks)),
        reranker=reranker,
        settings=_settings(advanced_rag_enabled=True),
    )
    result = await pipeline.retrieve(
        RetrievalRequest(question="billing policy", user_id=uuid.uuid4(), top_k=2)
    )
    assert reranker.calls == [("billing policy", 3, 2)]
    assert [c.chunk.content for c in result.candidates] == ["third", "second"]
    assert all(c.rerank_score is not None for c in result.candidates)
    assert [c.final_score for c in result.candidates] == [
        c.rerank_score for c in result.candidates
    ]


@pytest.mark.anyio
async def test_pipeline_skips_rerank_when_advanced_flag_off() -> None:
    chunks = [_chunk(content="a", score=0.9), _chunk(content="b", score=0.1)]
    reranker = FakeReranker()
    pipeline = DefaultAdvancedRetrievalPipeline(
        retriever=cast(Retriever, _CapturingRetriever(chunks)),
        reranker=reranker,
        settings=_settings(advanced_rag_enabled=False),
    )
    result = await pipeline.retrieve(
        RetrievalRequest(question="q", user_id=uuid.uuid4())
    )
    assert reranker.calls == []
    assert [c.chunk.content for c in result.candidates] == ["a", "b"]
    assert all(c.rerank_score is None for c in result.candidates)


@pytest.mark.anyio
async def test_pipeline_skips_rerank_without_reranker() -> None:
    chunks = [_chunk(content="only", score=0.5)]
    pipeline = DefaultAdvancedRetrievalPipeline(
        retriever=cast(Retriever, _CapturingRetriever(chunks)),
        settings=_settings(advanced_rag_enabled=True),
    )
    result = await pipeline.retrieve(
        RetrievalRequest(question="q", user_id=uuid.uuid4())
    )
    assert len(result.candidates) == 1
    assert result.candidates[0].rerank_score is None
    assert result.candidates[0].final_score == 0.5


@pytest.mark.anyio
async def test_pipeline_rerank_exception_keeps_pre_rerank_order(
    caplog: pytest.LogCaptureFixture,
) -> None:
    chunks = [
        _chunk(content="keep-first", score=0.9),
        _chunk(content="keep-second", score=0.8),
    ]
    reranker = FakeReranker(error=RuntimeError("boom"))
    caplog.set_level(logging.WARNING, logger="app.ai.rag.pipeline")
    pipeline = DefaultAdvancedRetrievalPipeline(
        retriever=cast(Retriever, _CapturingRetriever(chunks)),
        reranker=reranker,
        settings=_settings(advanced_rag_enabled=True),
    )
    result = await pipeline.retrieve(
        RetrievalRequest(question="q", user_id=uuid.uuid4())
    )
    assert [c.chunk.content for c in result.candidates] == [
        "keep-first",
        "keep-second",
    ]
    assert [c.final_score for c in result.candidates] == [0.9, 0.8]
    assert all(c.rerank_score is None for c in result.candidates)
    assert any(
        getattr(record, "rerank_failed", None) is True for record in caplog.records
    )


@pytest.mark.anyio
async def test_pipeline_uses_rag_top_k_when_request_top_k_omitted() -> None:
    chunks = [_chunk(content=f"c{i}", score=1.0 - i * 0.1) for i in range(6)]
    reranker = FakeReranker(reverse=False)
    pipeline = DefaultAdvancedRetrievalPipeline(
        retriever=cast(Retriever, _CapturingRetriever(chunks)),
        reranker=reranker,
        settings=_settings(advanced_rag_enabled=True),
    )
    await pipeline.retrieve(RetrievalRequest(question="q", user_id=uuid.uuid4()))
    assert reranker.calls == [("q", 6, 5)]


@pytest.mark.anyio
async def test_fake_reranker_sets_final_score_from_rerank_score() -> None:
    """Downstream stages must consume final_score only (set from rerank)."""
    candidates = [
        _candidate(content="low", final_score=0.1),
        _candidate(content="high", final_score=0.9),
    ]
    reranker = FakeReranker(reverse=True)
    out = await reranker.rerank("q", candidates, top_n=2)
    assert [c.chunk.content for c in out] == ["high", "low"]
    assert out[0].final_score == out[0].rerank_score == 1.0
    assert out[1].final_score == out[1].rerank_score == 0.9
