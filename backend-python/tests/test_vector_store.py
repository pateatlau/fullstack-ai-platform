"""PgVectorStore integration tests (requires pgvector-enabled Postgres)."""

from __future__ import annotations

import logging
import uuid

import pytest
from sqlalchemy import text

from app.ai.documents.schemas import DocumentChunk
from app.ai.vectorstores.pgvector import PgVectorStore
from app.core.config import Settings
from app.db.documents import SqlDocumentStore
from app.db.identity import SqlUserStore

DIMENSIONS = 1536


def _unit_vector(index: int) -> list[float]:
    vector = [0.0] * DIMENSIONS
    vector[index] = 1.0
    return vector


async def _pgvector_available(session) -> bool:
    try:
        result = await session.scalar(
            text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        )
        return result == 1
    except Exception:
        return False


async def _make_user(session) -> uuid.UUID:
    user = await SqlUserStore(session).create(
        sub=f"vector-{uuid.uuid4()}",
        email=None,
        name=None,
        picture=None,
    )
    return user.id


async def _seed_document_with_chunks(
    session,
    *,
    user_id: uuid.UUID,
    chunks: list[tuple[int, str, list[float]]],
) -> uuid.UUID:
    store = SqlDocumentStore(session)
    document = await store.create_document(
        user_id=user_id,
        filename="fixture.txt",
        mime_type="text/plain",
        status="ready",
    )
    await store.add_chunks(
        document.id,
        [(index, content, {"source": "fixture.txt"}) for index, content, _ in chunks],
    )
    vector_store = PgVectorStore(session, Settings(openai_api_key="test-key"))
    pipeline_chunks = [
        DocumentChunk(
            chunk_index=index,
            content=content,
            metadata={"source": "fixture.txt"},
            embedding=embedding,
        )
        for index, content, embedding in chunks
    ]
    await vector_store.upsert(
        document_id=document.id,
        user_id=user_id,
        chunks=pipeline_chunks,
    )
    return document.id


@pytest.fixture
async def pgvector_session(db_session):
    if not await _pgvector_available(db_session):
        pytest.skip("pgvector extension not available — run alembic upgrade head")
    yield db_session


@pytest.mark.anyio
async def test_pgvector_migration_extension_column_and_index(pgvector_session) -> None:
    extension = await pgvector_session.scalar(
        text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
    )
    assert extension == 1

    column = await pgvector_session.execute(
        text(
            """
            SELECT udt_name
            FROM information_schema.columns
            WHERE table_name = 'document_chunks' AND column_name = 'embedding'
            """
        )
    )
    assert column.scalar_one() == "vector"

    index = await pgvector_session.scalar(
        text(
            """
            SELECT 1
            FROM pg_indexes
            WHERE indexname = 'ix_document_chunks_embedding_hnsw'
            """
        )
    )
    assert index == 1


@pytest.mark.anyio
async def test_pgvector_store_upsert_persists_embeddings(pgvector_session) -> None:
    user_id = await _make_user(pgvector_session)
    document_id = await _seed_document_with_chunks(
        pgvector_session,
        user_id=user_id,
        chunks=[
            (0, "alpha chunk", _unit_vector(0)),
            (1, "beta chunk", _unit_vector(1)),
        ],
    )

    rows = await SqlDocumentStore(pgvector_session).list_chunks(document_id)
    assert len(rows) == 2
    assert all(row.embedding is not None for row in rows)


@pytest.mark.anyio
async def test_pgvector_store_similarity_search_top_k_ordering(
    pgvector_session,
) -> None:
    user_id = await _make_user(pgvector_session)
    await _seed_document_with_chunks(
        pgvector_session,
        user_id=user_id,
        chunks=[
            (0, "closest", _unit_vector(0)),
            (1, "middle", _unit_vector(1)),
            (2, "farthest", _unit_vector(2)),
        ],
    )
    store = PgVectorStore(pgvector_session, Settings(openai_api_key="test-key"))

    results = await store.similarity_search(
        _unit_vector(0),
        top_k=2,
        user_id=user_id,
    )

    assert len(results) == 2
    assert results[0].chunk_index == 0
    assert results[0].score >= results[1].score


@pytest.mark.anyio
async def test_pgvector_store_owner_isolation(pgvector_session) -> None:
    owner_id = await _make_user(pgvector_session)
    other_id = await _make_user(pgvector_session)
    await _seed_document_with_chunks(
        pgvector_session,
        user_id=owner_id,
        chunks=[(0, "private", _unit_vector(0))],
    )
    store = PgVectorStore(pgvector_session, Settings(openai_api_key="test-key"))

    owner_results = await store.similarity_search(
        _unit_vector(0),
        top_k=5,
        user_id=owner_id,
    )
    other_results = await store.similarity_search(
        _unit_vector(0),
        top_k=5,
        user_id=other_id,
    )

    assert len(owner_results) == 1
    assert other_results == []


@pytest.mark.anyio
async def test_pgvector_store_delete_by_document(pgvector_session) -> None:
    user_id = await _make_user(pgvector_session)
    document_id = await _seed_document_with_chunks(
        pgvector_session,
        user_id=user_id,
        chunks=[(0, "to delete", _unit_vector(0))],
    )
    store = PgVectorStore(pgvector_session, Settings(openai_api_key="test-key"))

    await store.delete_by_document(document_id)

    chunks = await SqlDocumentStore(pgvector_session).list_chunks(document_id)
    assert chunks == []
    assert (
        await store.similarity_search(
            _unit_vector(0),
            top_k=5,
            user_id=user_id,
        )
        == []
    )


@pytest.mark.anyio
async def test_pgvector_store_logs_latency_not_content(
    pgvector_session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="app.ai.vectorstores.pgvector")
    user_id = await _make_user(pgvector_session)
    secret = "classified-chunk-body"
    await _seed_document_with_chunks(
        pgvector_session,
        user_id=user_id,
        chunks=[(0, secret, _unit_vector(0))],
    )
    store = PgVectorStore(pgvector_session, Settings(openai_api_key="test-key"))

    await store.similarity_search(_unit_vector(0), top_k=1, user_id=user_id)

    records = [
        record
        for record in caplog.records
        if record.name == "app.ai.vectorstores.pgvector"
    ]
    assert records
    assert getattr(records[0], "vector_search_latency_ms") is not None
    assert getattr(records[0], "result_count") == 1
    assert secret not in caplog.text
    assert "1.0" not in caplog.text
