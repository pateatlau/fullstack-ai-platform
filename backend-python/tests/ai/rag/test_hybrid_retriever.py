"""Unit tests for HybridRetriever (Epic 02 Phase 4)."""

from __future__ import annotations

import logging
import uuid
from unittest.mock import AsyncMock

import pytest

from app.ai.interfaces.vector_store import ScoredChunk
from app.ai.rag.hybrid.retriever import HybridRetriever
from app.ai.rag.schemas import MetadataFilter
from app.core.config import Settings


def _chunk(
    *,
    chunk_id: uuid.UUID | None = None,
    content: str = "body",
    score: float = 0.5,
    chunk_index: int = 0,
) -> ScoredChunk:
    return ScoredChunk(
        chunk_id=chunk_id or uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=chunk_index,
        content=content,
        metadata={"source": "doc.txt"},
        score=score,
    )


def _settings() -> Settings:
    return Settings(
        openai_api_key="test-key",
        hybrid_dense_top_k=20,
        hybrid_lexical_top_k=20,
        rrf_k=60,
    )


@pytest.mark.anyio
async def test_hybrid_both_channels_sets_scores_and_orders_by_final_score() -> None:
    shared_id = uuid.uuid4()
    dense_only = _chunk(content="dense-only", score=0.9, chunk_index=0)
    shared_dense = _chunk(
        chunk_id=shared_id, content="shared", score=0.8, chunk_index=1
    )
    shared_lexical = _chunk(
        chunk_id=shared_id, content="shared", score=0.7, chunk_index=1
    )
    lexical_only = _chunk(content="lexical-only", score=0.6, chunk_index=2)

    embedding = AsyncMock()
    embedding.embed_texts = AsyncMock(return_value=[[0.1, 0.2]])
    store = AsyncMock()
    store.similarity_search = AsyncMock(return_value=[dense_only, shared_dense])
    store.lexical_search = AsyncMock(return_value=[shared_lexical, lexical_only])

    retriever = HybridRetriever(
        embedding_provider=embedding,
        vector_store=store,
        settings=_settings(),
    )
    user_id = uuid.uuid4()
    results = await retriever.retrieve(question="refund policy", user_id=user_id)

    assert [c.chunk.content for c in results] == [
        "shared",
        "dense-only",
        "lexical-only",
    ]
    shared = results[0]
    assert shared.final_score == shared.rrf_score
    assert shared.dense_score == pytest.approx(0.8)
    assert shared.lexical_score == pytest.approx(0.7)
    assert shared.final_score == pytest.approx(1 / 62 + 1 / 61)

    dense_cand = results[1]
    assert dense_cand.dense_score == pytest.approx(0.9)
    assert dense_cand.lexical_score is None
    assert dense_cand.final_score == dense_cand.rrf_score

    lexical_cand = results[2]
    assert lexical_cand.dense_score is None
    assert lexical_cand.lexical_score == pytest.approx(0.6)
    assert lexical_cand.final_score == lexical_cand.rrf_score

    # Downstream must not prefer diagnostic dense/lexical over final_score order.
    assert results[0].final_score >= results[1].final_score >= results[2].final_score

    store.similarity_search.assert_awaited_once()
    store.lexical_search.assert_awaited_once_with(
        "refund policy",
        top_k=20,
        user_id=user_id,
        filters=None,
    )


@pytest.mark.anyio
async def test_hybrid_dense_empty_keeps_lexical_candidates() -> None:
    lexical = _chunk(content="lexical-hit", score=0.55)
    embedding = AsyncMock()
    embedding.embed_texts = AsyncMock(return_value=[[0.1]])
    store = AsyncMock()
    store.similarity_search = AsyncMock(return_value=[])
    store.lexical_search = AsyncMock(return_value=[lexical])

    retriever = HybridRetriever(
        embedding_provider=embedding,
        vector_store=store,
        settings=_settings(),
    )
    results = await retriever.retrieve(question="q", user_id=uuid.uuid4())

    assert len(results) == 1
    assert results[0].chunk.content == "lexical-hit"
    assert results[0].dense_score is None
    assert results[0].lexical_score == pytest.approx(0.55)
    assert results[0].final_score == results[0].rrf_score


@pytest.mark.anyio
async def test_hybrid_lexical_empty_keeps_dense_candidates() -> None:
    dense = _chunk(content="dense-hit", score=0.95)
    embedding = AsyncMock()
    embedding.embed_texts = AsyncMock(return_value=[[0.1]])
    store = AsyncMock()
    store.similarity_search = AsyncMock(return_value=[dense])
    store.lexical_search = AsyncMock(return_value=[])

    retriever = HybridRetriever(
        embedding_provider=embedding,
        vector_store=store,
        settings=_settings(),
    )
    results = await retriever.retrieve(question="q", user_id=uuid.uuid4())

    assert len(results) == 1
    assert results[0].chunk.content == "dense-hit"
    assert results[0].dense_score == pytest.approx(0.95)
    assert results[0].lexical_score is None
    assert results[0].final_score == results[0].rrf_score


@pytest.mark.anyio
async def test_hybrid_unsatisfiable_filter_skips_store() -> None:
    embedding = AsyncMock()
    store = AsyncMock()
    retriever = HybridRetriever(
        embedding_provider=embedding,
        vector_store=store,
        settings=_settings(),
    )
    results = await retriever.retrieve(
        question="q",
        user_id=uuid.uuid4(),
        filters=MetadataFilter(document_ids=frozenset()),
    )
    assert results == []
    embedding.embed_texts.assert_not_called()
    store.similarity_search.assert_not_called()
    store.lexical_search.assert_not_called()


@pytest.mark.anyio
async def test_hybrid_logs_counts_not_query_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="app.ai.rag.hybrid.retriever")
    secret = "classified-user-question"
    embedding = AsyncMock()
    embedding.embed_texts = AsyncMock(return_value=[[0.1]])
    store = AsyncMock()
    store.similarity_search = AsyncMock(return_value=[])
    store.lexical_search = AsyncMock(return_value=[])

    retriever = HybridRetriever(
        embedding_provider=embedding,
        vector_store=store,
        settings=_settings(),
    )
    await retriever.retrieve(question=secret, user_id=uuid.uuid4())

    records = [
        record
        for record in caplog.records
        if record.name == "app.ai.rag.hybrid.retriever"
    ]
    assert records
    assert getattr(records[0], "hybrid_dense_count") == 0
    assert getattr(records[0], "hybrid_lexical_count") == 0
    assert getattr(records[0], "rrf_result_count") == 0
    assert secret not in caplog.text
