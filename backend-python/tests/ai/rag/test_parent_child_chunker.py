"""Parent-child chunking and parent expansion (Epic 02 Phase 2)."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence

import pytest

from app.ai.documents.chunkers.parent_child import ParentChildChunker
from app.ai.documents.chunkers.recursive import RecursiveChunker
from app.ai.documents.pipeline import IngestionPipeline
from app.ai.documents.schemas import ParsedDocument
from app.ai.interfaces.vector_store import ScoredChunk
from app.ai.rag.parent_expand import expand_parents
from app.ai.rag.schemas import RetrievedCandidate
from app.core.config import Settings


def _settings(
    *,
    advanced_rag_enabled: bool = False,
    child_chunk_size: int = 40,
    child_chunk_overlap: int = 8,
    parent_chunk_size: int = 80,
    parent_chunk_overlap: int = 10,
) -> Settings:
    return Settings(
        openai_api_key="test-key",
        advanced_rag_enabled=advanced_rag_enabled,
        child_chunk_size=child_chunk_size,
        child_chunk_overlap=child_chunk_overlap,
        parent_chunk_size=parent_chunk_size,
        parent_chunk_overlap=parent_chunk_overlap,
    )


def _scored(
    *,
    content: str,
    score: float,
    metadata: dict[str, object],
    chunk_id: uuid.UUID | None = None,
) -> ScoredChunk:
    return ScoredChunk(
        chunk_id=chunk_id or uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=0,
        content=content,
        metadata=metadata,
        score=score,
    )


def test_parent_child_chunker_links_children_to_parent_ids() -> None:
    text = " ".join(f"word{i:03d}" for i in range(60))
    document = ParsedDocument(text=text, metadata={"source": "sample.txt"})
    chunks = ParentChildChunker(_settings()).chunk(document)

    parents = [c for c in chunks if c.metadata.get("chunk_kind") == "parent"]
    children = [c for c in chunks if c.metadata.get("chunk_kind") == "child"]
    assert parents
    assert children
    assert all(c.id is not None for c in chunks)

    parent_ids = {str(p.id) for p in parents}
    for child in children:
        assert child.metadata["parent_id"] in parent_ids
        assert len(child.content) <= 40

    for parent in parents:
        assert "parent_id" not in parent.metadata
        assert len(parent.content) <= 80


def test_parent_child_chunker_propagates_source_and_page() -> None:
    document = ParsedDocument(
        text="Page one has enough text for a parent window.\n\n"
        "Page two continues with more words for children.",
        metadata={
            "source": "sample.pdf",
            "pages": [
                {
                    "page": 1,
                    "text": "Page one has enough text for a parent window.",
                },
                {
                    "page": 2,
                    "text": "Page two continues with more words for children.",
                },
            ],
        },
    )
    chunks = ParentChildChunker(
        _settings(
            parent_chunk_size=2000,
            parent_chunk_overlap=0,
            child_chunk_size=400,
            child_chunk_overlap=0,
        )
    ).chunk(document)

    assert chunks
    assert all(c.metadata["source"] == "sample.pdf" for c in chunks)
    children = [c for c in chunks if c.metadata.get("chunk_kind") == "child"]
    assert children
    assert all(c.metadata.get("page") in {1, 2} for c in children)


def test_ingestion_pipeline_selects_chunker_by_flag() -> None:
    off = IngestionPipeline(_settings(advanced_rag_enabled=False))
    on = IngestionPipeline(_settings(advanced_rag_enabled=True))
    assert isinstance(off._chunker, RecursiveChunker)
    assert isinstance(on._chunker, ParentChildChunker)


@pytest.mark.anyio
async def test_expand_parents_dedupes_shared_parent() -> None:
    parent_id = uuid.uuid4()
    parent_text = "full parent window text"
    child_a = _scored(
        content="child a",
        score=0.9,
        metadata={"chunk_kind": "child", "parent_id": str(parent_id)},
    )
    child_b = _scored(
        content="child b",
        score=0.8,
        metadata={"chunk_kind": "child", "parent_id": str(parent_id)},
    )
    candidates = [
        RetrievedCandidate(
            chunk=child_a,
            parent=None,
            metadata=dict(child_a.metadata),
            final_score=0.9,
        ),
        RetrievedCandidate(
            chunk=child_b,
            parent=None,
            metadata=dict(child_b.metadata),
            final_score=0.8,
        ),
    ]

    async def fetch(ids: Sequence[uuid.UUID]) -> Mapping[uuid.UUID, str]:
        assert list(ids) == [parent_id]
        return {parent_id: parent_text}

    expanded = await expand_parents(candidates, fetch_parent_contents=fetch)

    assert len(expanded) == 1
    assert expanded[0].parent == parent_text
    assert expanded[0].chunk.content == "child a"
    assert expanded[0].final_score == 0.9


@pytest.mark.anyio
async def test_expand_parents_orphan_keeps_child_block() -> None:
    missing_parent = uuid.uuid4()
    orphan = _scored(
        content="orphan child body",
        score=0.5,
        metadata={"chunk_kind": "child", "parent_id": str(missing_parent)},
    )
    flat = _scored(
        content="flat chunk",
        score=0.4,
        metadata={"source": "legacy.txt"},
    )
    candidates = [
        RetrievedCandidate(
            chunk=orphan,
            parent=None,
            metadata=dict(orphan.metadata),
            final_score=0.5,
        ),
        RetrievedCandidate(
            chunk=flat,
            parent=None,
            metadata=dict(flat.metadata),
            final_score=0.4,
        ),
    ]

    async def fetch(ids: Sequence[uuid.UUID]) -> Mapping[uuid.UUID, str]:
        return {}

    expanded = await expand_parents(candidates, fetch_parent_contents=fetch)

    assert len(expanded) == 2
    assert expanded[0].parent is None
    assert expanded[0].chunk.content == "orphan child body"
    assert expanded[1].parent is None
    assert expanded[1].chunk.content == "flat chunk"


@pytest.mark.anyio
async def test_expand_parents_empty_input() -> None:
    async def fetch(ids: Sequence[uuid.UUID]) -> Mapping[uuid.UUID, str]:
        raise AssertionError("fetch should not be called")

    assert await expand_parents([], fetch_parent_contents=fetch) == []
