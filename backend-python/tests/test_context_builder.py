"""Unit tests for ContextBuilder character budget and formatting."""

from __future__ import annotations

import uuid

from app.ai.interfaces.vector_store import ScoredChunk
from app.ai.rag.context_builder import ContextBuilder
from app.core.config import Settings


def _chunk(
    *, index: int, content: str, score: float, source: str | None = "doc.txt"
) -> ScoredChunk:
    metadata: dict[str, object] = {}
    if source is not None:
        metadata["source"] = source
    return ScoredChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=index,
        content=content,
        metadata=metadata,
        score=score,
    )


def _builder(*, max_chars: int = 8000) -> ContextBuilder:
    return ContextBuilder(
        Settings(openai_api_key="test-key", rag_context_max_chars=max_chars)
    )


def test_context_builder_under_budget_includes_all_chunks() -> None:
    chunks = [
        _chunk(index=0, content="first chunk", score=0.95),
        _chunk(index=1, content="second chunk", score=0.80),
    ]
    builder = _builder(max_chars=8000)

    result = builder.build(chunks)

    assert result.truncated is False
    assert result.included_chunks == chunks
    assert "[1]" in result.text
    assert "[2]" in result.text
    assert "first chunk" in result.text
    assert "second chunk" in result.text


def test_context_builder_over_budget_drops_lowest_scoring_chunks() -> None:
    chunks = [
        _chunk(index=0, content="keep-me", score=0.95),
        _chunk(index=1, content="drop-me", score=0.50),
    ]
    # Budget fits only the first numbered block.
    first_block = "[1] (source: doc.txt)\nkeep-me"
    builder = _builder(max_chars=len(first_block))

    result = builder.build(chunks)

    assert result.truncated is True
    assert len(result.included_chunks) == 1
    assert result.included_chunks[0].content == "keep-me"
    assert "drop-me" not in result.text


def test_context_builder_empty_list_returns_empty_context() -> None:
    builder = _builder()

    result = builder.build([])

    assert result.text == ""
    assert result.included_chunks == []
    assert result.truncated is False


def test_context_builder_numbered_block_format_with_source() -> None:
    chunks = [_chunk(index=0, content="body text", score=1.0, source="report.pdf")]
    builder = _builder()

    result = builder.build(chunks)

    assert result.text == "[1] (source: report.pdf)\nbody text"


def test_context_builder_numbered_block_without_source_metadata() -> None:
    chunks = [_chunk(index=0, content="body text", score=1.0, source=None)]
    builder = _builder()

    result = builder.build(chunks)

    assert result.text == "[1]\nbody text"


def test_context_builder_respects_explicit_max_chars_override() -> None:
    chunks = [
        _chunk(index=0, content="alpha", score=0.9),
        _chunk(index=1, content="beta", score=0.8),
    ]
    first_block = "[1] (source: doc.txt)\nalpha"
    builder = _builder(max_chars=8000)

    result = builder.build(chunks, max_chars=len(first_block))

    assert result.truncated is True
    assert len(result.included_chunks) == 1
