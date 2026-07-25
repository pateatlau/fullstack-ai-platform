"""Tests for CitationBuilder, pipeline cite wiring, and HTTP mapping (Phase 8)."""

from __future__ import annotations

import logging
import uuid
from typing import cast

import pytest

from app.ai.interfaces.vector_store import ScoredChunk
from app.ai.rag.citations import CitationBuilder, to_citation_schemas
from app.ai.rag.compress import FaithfulContextCompressor
from app.ai.rag.pipeline import DefaultAdvancedRetrievalPipeline
from app.ai.rag.retriever import Retriever
from app.ai.rag.schemas import (
    Citation,
    RAGResponse,
    RetrievalRequest,
    RetrievedCandidate,
)
from app.core.config import Settings
from app.routers.rag import _to_response
from app.schemas.chat import CitationSchema, RetrievalCompleteFrame


def _settings(
    *,
    advanced_rag_enabled: bool = True,
    citation_snippet_max_chars: int = 240,
    rag_context_max_chars: int = 8000,
) -> Settings:
    return Settings(
        openai_api_key="test-key",
        advanced_rag_enabled=advanced_rag_enabled,
        citation_snippet_max_chars=citation_snippet_max_chars,
        rag_context_max_chars=rag_context_max_chars,
    )


def _chunk(
    *,
    content: str,
    score: float = 0.5,
    source: str | None = "doc.txt",
    filename: str | None = None,
    page: int | str | None = None,
    chunk_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
) -> ScoredChunk:
    metadata: dict[str, object] = {}
    if source is not None:
        metadata["source"] = source
    if filename is not None:
        metadata["filename"] = filename
    if page is not None:
        metadata["page"] = page
    return ScoredChunk(
        chunk_id=chunk_id or uuid.uuid4(),
        document_id=document_id or uuid.uuid4(),
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
    filename: str | None = None,
    page: int | str | None = None,
    chunk_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
) -> RetrievedCandidate:
    chunk = _chunk(
        content=content,
        score=final_score,
        source=source,
        filename=filename,
        page=page,
        chunk_id=chunk_id,
        document_id=document_id,
    )
    return RetrievedCandidate(
        chunk=chunk,
        parent=parent,
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


def test_citation_builder_assigns_contiguous_indices_after_include_order() -> None:
    low = _candidate(content="low body", final_score=0.1, source="a.txt")
    high = _candidate(
        content="high body",
        final_score=0.9,
        source="b.txt",
        page=2,
        filename="report.pdf",
    )
    # Included order is post-compression order (not original retrieve order).
    included = [high.chunk, low.chunk]
    citations = CitationBuilder(settings=_settings()).build(
        included, candidates=[low, high]
    )

    assert [c.index for c in citations] == [1, 2]
    assert citations[0].chunk_id == high.chunk.chunk_id
    assert citations[0].score == 0.9
    assert citations[0].filename == "report.pdf"
    assert citations[0].source == "b.txt"
    assert citations[0].page == 2
    assert citations[0].snippet == "high body"
    assert citations[1].chunk_id == low.chunk.chunk_id
    assert citations[1].score == 0.1


def test_citation_builder_uses_final_score_not_chunk_score() -> None:
    candidate = _candidate(content="body", final_score=0.77)
    # Simulate a stale ScoredChunk.score that must not drive citation.score.
    included = [
        ScoredChunk(
            chunk_id=candidate.chunk.chunk_id,
            document_id=candidate.chunk.document_id,
            chunk_index=0,
            content=candidate.chunk.content,
            metadata=dict(candidate.chunk.metadata),
            score=0.11,
        )
    ]
    citations = CitationBuilder(settings=_settings()).build(
        included, candidates=[candidate]
    )
    assert citations[0].score == 0.77


def test_citation_builder_snippet_from_original_parent_not_trimmed() -> None:
    parent = "ORIGINAL PARENT TEXT THAT IS LONG ENOUGH TO TRIM"
    candidate = _candidate(
        content="child slice",
        final_score=1.0,
        parent=parent,
    )
    trimmed = ScoredChunk(
        chunk_id=candidate.chunk.chunk_id,
        document_id=candidate.chunk.document_id,
        chunk_index=0,
        content=parent[:10],
        metadata=dict(candidate.chunk.metadata),
        score=1.0,
    )
    citations = CitationBuilder(
        settings=_settings(citation_snippet_max_chars=20)
    ).build([trimmed], candidates=[candidate])

    assert citations[0].snippet == parent[:20]
    assert "child slice" not in citations[0].snippet


def test_citation_builder_bounds_snippet() -> None:
    text = "x" * 500
    candidate = _candidate(content=text, final_score=0.5)
    citations = CitationBuilder(
        settings=_settings(citation_snippet_max_chars=240)
    ).build([candidate.chunk], candidates=[candidate])
    assert len(citations[0].snippet) == 240
    assert citations[0].snippet == text[:240]


def test_citation_builder_empty_included() -> None:
    assert CitationBuilder(settings=_settings()).build([]) == []


def test_citation_builder_logs_without_raw_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "SECRET_SNIPPET_BODY_MUST_NOT_APPEAR_IN_LOGS"
    candidate = _candidate(content=secret, final_score=1.0)
    caplog.set_level(logging.INFO, logger="app.ai.rag.citations.builder")

    CitationBuilder(settings=_settings()).build(
        [candidate.chunk], candidates=[candidate]
    )

    combined = " ".join(record.getMessage() for record in caplog.records)
    assert secret not in combined


@pytest.mark.anyio
async def test_pipeline_cites_after_compression_indices_match_context() -> None:
    high = _chunk(content="keep-high", score=0.99)
    low = _chunk(content="drop-low", score=0.01)
    max_chars = len("[1] (source: doc.txt)\nkeep-high")
    settings = _settings(
        advanced_rag_enabled=True,
        rag_context_max_chars=max_chars,
    )
    pipeline = DefaultAdvancedRetrievalPipeline(
        retriever=cast(Retriever, _CapturingRetriever([low, high])),
        context_compressor=FaithfulContextCompressor(settings=settings),
        settings=settings,
    )
    result = await pipeline.retrieve(
        RetrievalRequest(question="q", user_id=uuid.uuid4())
    )

    assert "keep-high" in result.context_text
    assert "drop-low" not in result.context_text
    assert result.context_text.startswith("[1]")
    assert len(result.citations) == 1
    assert result.citations[0].index == 1
    assert result.citations[0].chunk_id == high.chunk_id
    assert result.citations[0].score == 0.99
    assert result.citations[0].snippet == "keep-high"


@pytest.mark.anyio
async def test_pipeline_skips_cite_when_advanced_flag_off() -> None:
    chunks = [_chunk(content="a", score=0.9)]
    settings = _settings(advanced_rag_enabled=False)
    pipeline = DefaultAdvancedRetrievalPipeline(
        retriever=cast(Retriever, _CapturingRetriever(chunks)),
        context_compressor=FaithfulContextCompressor(settings=settings),
        settings=settings,
    )
    result = await pipeline.retrieve(
        RetrievalRequest(question="q", user_id=uuid.uuid4())
    )
    assert result.citations == ()
    assert result.context_text == ""


@pytest.mark.anyio
async def test_pipeline_skips_cite_without_compressor() -> None:
    chunks = [_chunk(content="only", score=0.5)]
    pipeline = DefaultAdvancedRetrievalPipeline(
        retriever=cast(Retriever, _CapturingRetriever(chunks)),
        settings=_settings(advanced_rag_enabled=True),
    )
    result = await pipeline.retrieve(
        RetrievalRequest(question="q", user_id=uuid.uuid4())
    )
    assert result.citations == ()


def test_to_citation_schemas_none_passthrough() -> None:
    assert to_citation_schemas(None) is None


def test_to_citation_schemas_maps_fields() -> None:
    citation = Citation(
        index=1,
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        snippet="excerpt",
        score=0.88,
        filename="a.md",
        source="a.md",
        page=4,
    )
    schemas = to_citation_schemas([citation])
    assert schemas is not None
    assert len(schemas) == 1
    assert isinstance(schemas[0], CitationSchema)
    assert schemas[0].model_dump() == {
        "index": 1,
        "chunk_id": citation.chunk_id,
        "document_id": citation.document_id,
        "snippet": "excerpt",
        "score": 0.88,
        "filename": "a.md",
        "source": "a.md",
        "page": 4,
    }


def test_rag_ask_response_serializes_citations() -> None:
    chunk_id = uuid.uuid4()
    document_id = uuid.uuid4()
    response = RAGResponse(
        answer="ok",
        retrieved_chunks=[],
        truncated=False,
        model="gpt-4o-mini",
        provider="openai",
        citations=[
            Citation(
                index=1,
                chunk_id=chunk_id,
                document_id=document_id,
                snippet="snip",
                score=0.5,
                source="f.txt",
            )
        ],
    )
    body = _to_response(response).model_dump(mode="json")
    assert body["citations"] == [
        {
            "index": 1,
            "chunk_id": str(chunk_id),
            "document_id": str(document_id),
            "snippet": "snip",
            "score": 0.5,
            "filename": None,
            "source": "f.txt",
            "page": None,
        }
    ]


def test_rag_ask_response_citations_null_when_absent() -> None:
    response = RAGResponse(
        answer="ok",
        retrieved_chunks=[],
        truncated=False,
        model="gpt-4o-mini",
        provider="openai",
        citations=None,
    )
    body = _to_response(response).model_dump(mode="json")
    assert body["citations"] is None


def test_retrieval_complete_frame_includes_citation_count() -> None:
    frame = RetrievalCompleteFrame(id="resp_1", chunk_count=3, citation_count=2)
    payload = frame.model_dump(mode="json")
    assert payload["chunk_count"] == 3
    assert payload["citation_count"] == 2


def test_retrieval_complete_frame_citation_count_defaults_zero() -> None:
    frame = RetrievalCompleteFrame(id="resp_1", chunk_count=1)
    assert frame.citation_count == 0


@pytest.mark.anyio
async def test_rag_service_v1_path_leaves_citations_null(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock

    from app.ai.prompts.manager import create_prompt_manager
    from app.ai.rag.context_builder import ContextBuilder
    from app.ai.rag.prompt_builder import PromptBuilder
    from app.ai.rag.service import RAGService
    from app.providers.factory import ProviderFactory
    from tests.fakes import FakeProvider

    chunk = _chunk(content="ctx", score=0.9)
    embed = AsyncMock()
    embed.embed_texts = AsyncMock(return_value=[[0.1]])
    store = AsyncMock()
    store.similarity_search = AsyncMock(return_value=[chunk])
    settings = Settings(
        openai_api_key="test-key",
        rag_enabled=True,
        advanced_rag_enabled=False,
    )
    retriever = Retriever(
        embedding_provider=embed,
        vector_store=store,
        settings=settings,
    )
    llm = FakeProvider(response="answer")
    monkeypatch.setattr(
        ProviderFactory,
        "get_provider",
        lambda *_a, **_k: llm,
    )
    service = RAGService(
        retriever=retriever,
        context_builder=ContextBuilder(settings),
        prompt_builder=PromptBuilder(
            prompt_manager=create_prompt_manager(),
            settings=settings,
        ),
        settings=settings,
    )
    result = await service.ask(user_id=uuid.uuid4(), question="q?")
    assert result.citations is None
    assert result.retrieved_chunks
