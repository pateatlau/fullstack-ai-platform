"""End-to-end advanced pipeline stage wiring (Epic 02 Phase 10).

Observes rewrite → hybrid → filter → parent → rerank → compress → cite with
fakes (no Cohere / no live LLM rewrite).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from typing import cast
from unittest.mock import AsyncMock

import pytest

from app.ai.interfaces.query_rewriter import QueryRewriter
from app.ai.interfaces.reranker import Reranker
from app.ai.interfaces.vector_store import ScoredChunk
from app.ai.rag.citations import CitationBuilder
from app.ai.rag.compress import FaithfulContextCompressor
from app.ai.rag.hybrid.retriever import HybridRetriever
from app.ai.rag.pipeline import DefaultAdvancedRetrievalPipeline
from app.ai.rag.retriever import Retriever
from app.ai.rag.schemas import MetadataFilter, RetrievalRequest, RetrievedCandidate
from app.core.config import Settings


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "openai_api_key": "test-key",
        "advanced_rag_enabled": True,
        "query_rewrite_enabled": True,
        "rag_top_k": 5,
        "rag_context_max_chars": 8000,
        "citation_snippet_max_chars": 240,
        "hybrid_dense_top_k": 20,
        "hybrid_lexical_top_k": 20,
        "rrf_k": 60,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def _chunk(
    *,
    content: str,
    score: float,
    chunk_id: uuid.UUID | None = None,
    document_id: uuid.UUID | None = None,
    metadata: dict[str, object] | None = None,
    chunk_index: int = 0,
) -> ScoredChunk:
    return ScoredChunk(
        chunk_id=chunk_id or uuid.uuid4(),
        document_id=document_id or uuid.uuid4(),
        chunk_index=chunk_index,
        content=content,
        metadata=metadata or {"source": "doc.txt"},
        score=score,
    )


class _CountingRewriter:
    def __init__(self, rewritten: str) -> None:
        self.rewritten = rewritten
        self.calls: list[str] = []

    async def rewrite(self, query: str, *, user_id: uuid.UUID) -> str:
        _ = user_id
        self.calls.append(query)
        return self.rewritten


class _FakeReranker:
    def __init__(self) -> None:
        self.calls = 0
        self.queries: list[str] = []

    async def rerank(
        self,
        query: str,
        candidates: list[RetrievedCandidate],
        *,
        top_n: int,
    ) -> list[RetrievedCandidate]:
        self.calls += 1
        self.queries.append(query)
        # Reverse order and set rerank_score / final_score to prove stage ran.
        reordered = list(reversed(candidates[:top_n]))
        return [
            RetrievedCandidate(
                chunk=c.chunk,
                parent=c.parent,
                metadata=dict(c.metadata),
                final_score=float(len(reordered) - i),
                dense_score=c.dense_score,
                lexical_score=c.lexical_score,
                rrf_score=c.rrf_score,
                rerank_score=float(len(reordered) - i),
            )
            for i, c in enumerate(reordered)
        ]


@pytest.mark.anyio
async def test_advanced_pipeline_stage_graph_happy_path(
    caplog: pytest.LogCaptureFixture,
) -> None:
    parent_id = uuid.uuid4()
    child_a_id = uuid.uuid4()
    child_b_id = uuid.uuid4()
    orphan_id = uuid.uuid4()
    doc_id = uuid.uuid4()

    dense_child_a = _chunk(
        chunk_id=child_a_id,
        document_id=doc_id,
        content="child-a slice",
        score=0.9,
        metadata={
            "chunk_kind": "child",
            "parent_id": str(parent_id),
            "source": "policy.pdf",
            "filename": "policy.pdf",
            "tags": ["refund"],
        },
    )
    dense_child_b = _chunk(
        chunk_id=child_b_id,
        document_id=doc_id,
        content="child-b slice",
        score=0.8,
        metadata={
            "chunk_kind": "child",
            "parent_id": str(parent_id),
            "source": "policy.pdf",
            "filename": "policy.pdf",
            "tags": ["refund"],
        },
    )
    dense_orphan = _chunk(
        chunk_id=orphan_id,
        document_id=doc_id,
        content="orphan flat chunk about shipping",
        score=0.7,
        metadata={
            "source": "shipping.txt",
            "filename": "shipping.txt",
            "tags": ["shipping"],
        },
    )
    # Lexical-only hit shares parent with dense children (dedupe target).
    lexical_child_b = _chunk(
        chunk_id=child_b_id,
        document_id=doc_id,
        content="child-b slice",
        score=0.6,
        metadata=dict(dense_child_b.metadata),
    )

    embedding = AsyncMock()
    embedding.embed_texts = AsyncMock(return_value=[[0.1, 0.2]])
    store = AsyncMock()
    store.similarity_search = AsyncMock(
        return_value=[dense_child_a, dense_child_b, dense_orphan]
    )
    store.lexical_search = AsyncMock(return_value=[lexical_child_b])

    parent_fetches: list[list[uuid.UUID]] = []

    async def fetch_parents(ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, str]:
        parent_fetches.append(list(ids))
        return {parent_id: "FULL PARENT POLICY TEXT about refunds and timelines."}

    rewriter = _CountingRewriter("rewritten: refund timeline")
    reranker = _FakeReranker()
    settings = _settings()
    hybrid = HybridRetriever(
        embedding_provider=embedding,
        vector_store=store,
        settings=settings,
    )
    pipeline = DefaultAdvancedRetrievalPipeline(
        hybrid_retriever=hybrid,
        parent_content_fetcher=fetch_parents,
        query_rewriter=cast(QueryRewriter, rewriter),
        reranker=cast(Reranker, reranker),
        context_compressor=FaithfulContextCompressor(settings=settings),
        citation_builder=CitationBuilder(settings=settings),
        settings=settings,
    )

    user_id = uuid.uuid4()
    with caplog.at_level(logging.INFO):
        result = await pipeline.retrieve(
            RetrievalRequest(
                question="when do I get my money back?",
                user_id=user_id,
                top_k=5,
                filters=MetadataFilter(tags=frozenset({"refund"})),
            )
        )

    # 1. Rewrite once with original question.
    assert rewriter.calls == ["when do I get my money back?"]
    # 2. Hybrid used rewritten query on both channels.
    store.similarity_search.assert_awaited()
    store.lexical_search.assert_awaited()
    assert store.similarity_search.await_args.kwargs["user_id"] == user_id
    assert store.lexical_search.await_args.args[0] == "rewritten: refund timeline"
    # Embed used rewritten query (dense channel).
    embedding.embed_texts.assert_awaited_with(["rewritten: refund timeline"])

    # 3. Metadata filter dropped orphan (tags=shipping); parent children kept.
    for candidate in result.candidates:
        tags = candidate.metadata.get("tags")
        assert isinstance(tags, list)
        assert "refund" in tags
    assert orphan_id not in {c.chunk.chunk_id for c in result.candidates}

    # 4. Parent expand deduped shared parent → one candidate with parent text.
    assert parent_fetches and parent_id in parent_fetches[0]
    parent_candidates = [c for c in result.candidates if c.parent is not None]
    assert len(parent_candidates) == 1
    assert parent_candidates[0].parent is not None
    assert "FULL PARENT POLICY TEXT" in parent_candidates[0].parent

    # 5. Rerank ran on rewritten query.
    assert reranker.calls == 1
    assert reranker.queries == ["rewritten: refund timeline"]
    assert all(c.rerank_score is not None for c in result.candidates)

    # 6–7. Compress + cite: context uses parent text; contiguous citations.
    assert result.context_text
    assert "FULL PARENT POLICY TEXT" in result.context_text
    assert len(result.citations) >= 1
    assert [c.index for c in result.citations] == list(
        range(1, len(result.citations) + 1)
    )
    assert result.citations[0].score == result.candidates[0].final_score
    assert any(
        "Advanced retrieval completed" in record.message for record in caplog.records
    )


@pytest.mark.anyio
async def test_pipeline_requires_hybrid_or_dense_retriever() -> None:
    with pytest.raises(ValueError, match="hybrid_retriever or retriever"):
        DefaultAdvancedRetrievalPipeline(settings=_settings())


@pytest.mark.anyio
async def test_dense_fallback_still_works_without_hybrid() -> None:
    """Unit-test path: dense Retriever stub when hybrid is not injected."""
    chunk = _chunk(content="dense-only body", score=0.5)
    retriever = AsyncMock()
    retriever.retrieve = AsyncMock(return_value=[chunk])
    settings = _settings()
    pipeline = DefaultAdvancedRetrievalPipeline(
        retriever=cast(Retriever, retriever),
        context_compressor=FaithfulContextCompressor(settings=settings),
        settings=settings,
    )
    result = await pipeline.retrieve(
        RetrievalRequest(question="q", user_id=uuid.uuid4())
    )
    assert len(result.candidates) == 1
    assert result.candidates[0].chunk.content == "dense-only body"
    assert len(result.citations) == 1
