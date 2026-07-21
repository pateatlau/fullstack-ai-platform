"""Unit tests for Retriever (mocked embedding provider and vector store)."""

from __future__ import annotations

import logging
import uuid
from unittest.mock import AsyncMock

import pytest

from app.ai.interfaces.vector_store import ScoredChunk
from app.ai.rag.retriever import Retriever
from app.core.config import Settings


def _chunk(*, index: int, content: str, score: float) -> ScoredChunk:
    return ScoredChunk(
        chunk_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        chunk_index=index,
        content=content,
        metadata={"source": "fixture.txt"},
        score=score,
    )


def _retriever(
    *,
    settings: Settings | None = None,
    embedding_provider: AsyncMock | None = None,
    vector_store: AsyncMock | None = None,
) -> tuple[Retriever, AsyncMock, AsyncMock, Settings]:
    resolved_settings = settings or Settings(
        openai_api_key="test-key",
        rag_top_k=5,
    )
    embed = embedding_provider or AsyncMock()
    store = vector_store or AsyncMock()
    return (
        Retriever(
            embedding_provider=embed,
            vector_store=store,
            settings=resolved_settings,
        ),
        embed,
        store,
        resolved_settings,
    )


@pytest.mark.anyio
async def test_retriever_returns_ranked_chunks_for_known_query_embedding() -> None:
    user_id = uuid.uuid4()
    query_vector = [0.1, 0.2, 0.3]
    expected = [
        _chunk(index=0, content="alpha", score=0.95),
        _chunk(index=1, content="beta", score=0.80),
    ]
    embed = AsyncMock()
    embed.embed_texts = AsyncMock(return_value=[query_vector])
    store = AsyncMock()
    store.similarity_search = AsyncMock(return_value=expected)
    retriever, _, _, _ = _retriever(embedding_provider=embed, vector_store=store)

    results = await retriever.retrieve(
        question="What is alpha?",
        user_id=user_id,
    )

    assert results == expected
    embed.embed_texts.assert_awaited_once_with(["What is alpha?"])
    store.similarity_search.assert_awaited_once_with(
        query_vector,
        top_k=5,
        user_id=user_id,
    )


@pytest.mark.anyio
async def test_retriever_empty_corpus_returns_empty_list() -> None:
    embed = AsyncMock()
    embed.embed_texts = AsyncMock(return_value=[[0.1, 0.2]])
    store = AsyncMock()
    store.similarity_search = AsyncMock(return_value=[])
    retriever, _, _, _ = _retriever(embedding_provider=embed, vector_store=store)

    results = await retriever.retrieve(
        question="anything",
        user_id=uuid.uuid4(),
    )

    assert results == []


@pytest.mark.anyio
async def test_retriever_empty_embedding_returns_empty_list() -> None:
    embed = AsyncMock()
    embed.embed_texts = AsyncMock(return_value=[])
    store = AsyncMock()
    retriever, _, store, _ = _retriever(embedding_provider=embed, vector_store=store)

    results = await retriever.retrieve(
        question="anything",
        user_id=uuid.uuid4(),
    )

    assert results == []
    store.similarity_search.assert_not_called()


@pytest.mark.anyio
async def test_retriever_uses_rag_top_k_from_settings() -> None:
    settings = Settings(openai_api_key="test-key", rag_top_k=3)
    embed = AsyncMock()
    embed.embed_texts = AsyncMock(return_value=[[0.5]])
    store = AsyncMock()
    store.similarity_search = AsyncMock(return_value=[])
    retriever, _, _, _ = _retriever(
        settings=settings,
        embedding_provider=embed,
        vector_store=store,
    )

    await retriever.retrieve(question="query", user_id=uuid.uuid4())

    store.similarity_search.assert_awaited_once()
    assert store.similarity_search.await_args.kwargs["top_k"] == 3


@pytest.mark.anyio
async def test_retriever_passes_user_id_to_vector_store() -> None:
    user_id = uuid.uuid4()
    embed = AsyncMock()
    embed.embed_texts = AsyncMock(return_value=[[0.5]])
    store = AsyncMock()
    store.similarity_search = AsyncMock(return_value=[])
    retriever, _, _, _ = _retriever(embedding_provider=embed, vector_store=store)

    await retriever.retrieve(question="scoped query", user_id=user_id)

    assert store.similarity_search.await_args.kwargs["user_id"] == user_id


@pytest.mark.anyio
async def test_retriever_emits_retrieval_latency_ms(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="app.ai.rag.retriever")
    embed = AsyncMock()
    embed.embed_texts = AsyncMock(return_value=[[0.5]])
    store = AsyncMock()
    store.similarity_search = AsyncMock(
        return_value=[_chunk(index=0, content="x", score=1.0)]
    )
    retriever, _, _, _ = _retriever(embedding_provider=embed, vector_store=store)

    await retriever.retrieve(question="latency test", user_id=uuid.uuid4())

    records = [
        record for record in caplog.records if record.name == "app.ai.rag.retriever"
    ]
    assert records
    assert getattr(records[0], "retrieval_latency_ms") is not None
    assert getattr(records[0], "result_count") == 1


@pytest.mark.anyio
async def test_retriever_logs_no_sensitive_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="app.ai.rag.retriever")
    secret_question = "classified-user-question"
    secret_content = "classified-chunk-body"
    query_vector = [0.111, 0.222, 0.333]
    embed = AsyncMock()
    embed.embed_texts = AsyncMock(return_value=[query_vector])
    store = AsyncMock()
    store.similarity_search = AsyncMock(
        return_value=[_chunk(index=0, content=secret_content, score=0.9)]
    )
    retriever, _, _, _ = _retriever(embedding_provider=embed, vector_store=store)

    await retriever.retrieve(question=secret_question, user_id=uuid.uuid4())

    assert secret_question not in caplog.text
    assert secret_content not in caplog.text
    assert "0.111" not in caplog.text
