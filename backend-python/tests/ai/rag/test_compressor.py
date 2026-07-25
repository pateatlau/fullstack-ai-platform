"""Tests for FaithfulContextCompressor and pipeline compress wiring (Phase 7)."""

from __future__ import annotations

import logging
import uuid
from typing import cast

import pytest

from app.ai.interfaces.vector_store import ScoredChunk
from app.ai.rag.compress import FaithfulContextCompressor
from app.ai.rag.context_builder import BuiltContext, ContextBuilder
from app.ai.rag.pipeline import DefaultAdvancedRetrievalPipeline
from app.ai.rag.retriever import Retriever
from app.ai.rag.schemas import RetrievalRequest, RetrievedCandidate
from app.core.config import Settings


def _settings(
    *,
    advanced_rag_enabled: bool = True,
    rag_context_max_chars: int = 8000,
) -> Settings:
    return Settings(
        openai_api_key="test-key",
        advanced_rag_enabled=advanced_rag_enabled,
        rag_context_max_chars=rag_context_max_chars,
    )


def _chunk(
    *,
    content: str,
    score: float = 0.5,
    source: str | None = "doc.txt",
) -> ScoredChunk:
    metadata: dict[str, object] = {}
    if source is not None:
        metadata["source"] = source
    return ScoredChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=0,
        content=content,
        metadata=metadata,
        score=score,
    )


def _candidate(
    *,
    content: str,
    final_score: float,
    parent: str | None = None,
    source: str | None = "doc.txt",
) -> RetrievedCandidate:
    chunk = _chunk(content=content, score=final_score, source=source)
    return RetrievedCandidate(
        chunk=chunk,
        parent=parent,
        metadata=dict(chunk.metadata),
        final_score=final_score,
        dense_score=final_score,
    )


def _compressor(*, max_chars: int = 8000) -> FaithfulContextCompressor:
    return FaithfulContextCompressor(
        settings=_settings(rag_context_max_chars=max_chars)
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


class _CapturingCompressor:
    """Protocol-compliant compressor that records calls."""

    def __init__(self, built: BuiltContext | None = None) -> None:
        self.calls: list[tuple[int, int]] = []
        self._built = built

    def compress(
        self,
        candidates: list[RetrievedCandidate],
        *,
        max_chars: int,
    ) -> BuiltContext:
        self.calls.append((len(candidates), max_chars))
        if self._built is not None:
            return self._built
        chunks = [c.chunk for c in candidates]
        return BuiltContext(
            text="compressed",
            included_chunks=chunks,
            truncated=False,
        )


def test_compress_empty_input() -> None:
    result = _compressor().compress([], max_chars=100)
    assert result.text == ""
    assert result.included_chunks == []
    assert result.truncated is False


def test_compress_single_chunk_under_budget_text_unchanged() -> None:
    candidate = _candidate(content="exact body text", final_score=0.9)
    result = _compressor().compress([candidate], max_chars=8000)

    assert result.truncated is False
    assert len(result.included_chunks) == 1
    assert result.included_chunks[0].content == "exact body text"
    assert result.text == "[1] (source: doc.txt)\nexact body text"
    # Original source text appears unchanged (no paraphrase).
    assert "exact body text" in result.text


def test_compress_budget_fit_selects_by_final_score() -> None:
    low = _candidate(content="low-score-body", final_score=0.1)
    high = _candidate(content="high-score-body", final_score=0.9)
    # Budget fits only one numbered block — must keep higher final_score.
    first_block = "[1] (source: doc.txt)\nhigh-score-body"
    result = _compressor().compress([low, high], max_chars=len(first_block))

    assert result.truncated is True
    assert len(result.included_chunks) == 1
    assert result.included_chunks[0].content == "high-score-body"
    assert "low-score-body" not in result.text
    assert result.text == first_block


def test_compress_prefix_trim_preserves_original_prefix() -> None:
    body = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    candidate = _candidate(content=body, final_score=1.0)
    # Force trim: header + partial content only.
    header = "[1] (source: doc.txt)\n"
    max_chars = len(header) + 10
    result = _compressor().compress([candidate], max_chars=max_chars)

    assert result.truncated is True
    assert len(result.included_chunks) == 1
    included = result.included_chunks[0].content
    assert included == body[:10]
    assert body.startswith(included)
    assert result.text == f"{header}{included}"
    # Never invent / rearrange characters outside the original prefix.
    assert included in body


def test_compress_prefers_parent_text_when_set() -> None:
    candidate = _candidate(
        content="child-only",
        parent="PARENT BLOCK TEXT",
        final_score=0.8,
    )
    result = _compressor().compress([candidate], max_chars=8000)

    assert result.included_chunks[0].content == "PARENT BLOCK TEXT"
    assert "PARENT BLOCK TEXT" in result.text
    assert "child-only" not in result.text


def test_compress_text_unchanged_for_multiple_under_budget() -> None:
    a = _candidate(content="alpha source", final_score=0.9)
    b = _candidate(content="beta source", final_score=0.8)
    result = _compressor().compress([a, b], max_chars=8000)

    assert result.truncated is False
    assert [c.content for c in result.included_chunks] == [
        "alpha source",
        "beta source",
    ]
    assert result.text == (
        "[1] (source: doc.txt)\nalpha source\n\n[2] (source: doc.txt)\nbeta source"
    )


def test_compress_fallback_to_context_builder_when_pack_empty(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When even a trimmed block cannot fit, delegate to ContextBuilder."""
    candidate = _candidate(content="body", final_score=0.5)
    # Too small for any `[1] (source: …)\n` + content.
    max_chars = 3
    builder = ContextBuilder(_settings(rag_context_max_chars=max_chars))
    compressor = FaithfulContextCompressor(
        settings=_settings(rag_context_max_chars=max_chars),
        context_builder=builder,
    )
    caplog.set_level(logging.INFO, logger="app.ai.rag.compress.compressor")

    result = compressor.compress([candidate], max_chars=max_chars)

    # ContextBuilder also cannot fit → empty truncated context.
    assert result.text == ""
    assert result.included_chunks == []
    assert result.truncated is True
    assert any(
        getattr(record, "compression_fallback", None) is True
        for record in caplog.records
    )


def test_compress_does_not_log_raw_chunk_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "SECRET_DOCUMENT_BODY_SHOULD_NOT_APPEAR_IN_LOGS"
    candidate = _candidate(content=secret, final_score=1.0)
    caplog.set_level(logging.INFO, logger="app.ai.rag.compress.compressor")

    _compressor().compress([candidate], max_chars=8000)

    combined = " ".join(record.getMessage() for record in caplog.records)
    assert secret not in combined
    for record in caplog.records:
        assert secret not in str(record.__dict__)


@pytest.mark.anyio
async def test_pipeline_compresses_when_advanced_flag_on() -> None:
    chunks = [
        _chunk(content="first", score=0.9),
        _chunk(content="second", score=0.8),
    ]
    compressor = _CapturingCompressor()
    pipeline = DefaultAdvancedRetrievalPipeline(
        retriever=cast(Retriever, _CapturingRetriever(chunks)),
        context_compressor=compressor,
        settings=_settings(advanced_rag_enabled=True, rag_context_max_chars=1234),
    )
    result = await pipeline.retrieve(
        RetrievalRequest(question="q", user_id=uuid.uuid4())
    )
    assert compressor.calls == [(2, 1234)]
    assert result.context_text == "compressed"
    assert result.truncated is False


@pytest.mark.anyio
async def test_pipeline_skips_compress_when_advanced_flag_off() -> None:
    chunks = [_chunk(content="a", score=0.9)]
    compressor = _CapturingCompressor()
    pipeline = DefaultAdvancedRetrievalPipeline(
        retriever=cast(Retriever, _CapturingRetriever(chunks)),
        context_compressor=compressor,
        settings=_settings(advanced_rag_enabled=False),
    )
    result = await pipeline.retrieve(
        RetrievalRequest(question="q", user_id=uuid.uuid4())
    )
    assert compressor.calls == []
    assert result.context_text == ""
    assert result.truncated is False


@pytest.mark.anyio
async def test_pipeline_skips_compress_without_compressor() -> None:
    chunks = [_chunk(content="only", score=0.5)]
    pipeline = DefaultAdvancedRetrievalPipeline(
        retriever=cast(Retriever, _CapturingRetriever(chunks)),
        settings=_settings(advanced_rag_enabled=True),
    )
    result = await pipeline.retrieve(
        RetrievalRequest(question="q", user_id=uuid.uuid4())
    )
    assert result.context_text == ""
    assert result.truncated is False


@pytest.mark.anyio
async def test_pipeline_uses_faithful_compressor_end_to_end() -> None:
    high = _chunk(content="keep-high", score=0.99)
    low = _chunk(content="drop-low", score=0.01)
    max_chars = len("[1] (source: doc.txt)\nkeep-high")
    pipeline = DefaultAdvancedRetrievalPipeline(
        retriever=cast(Retriever, _CapturingRetriever([low, high])),
        context_compressor=FaithfulContextCompressor(
            settings=_settings(rag_context_max_chars=max_chars)
        ),
        settings=_settings(
            advanced_rag_enabled=True,
            rag_context_max_chars=max_chars,
        ),
    )
    result = await pipeline.retrieve(
        RetrievalRequest(question="q", user_id=uuid.uuid4())
    )
    assert result.truncated is True
    assert "keep-high" in result.context_text
    assert "drop-low" not in result.context_text
    # Candidates remain post-retrieve list; compression only fills context.
    assert len(result.candidates) == 2
