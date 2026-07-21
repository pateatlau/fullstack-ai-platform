"""KnowledgeService end-to-end ingest and delete tests."""

from __future__ import annotations

import inspect
import logging
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, text

from app.ai.documents.pipeline import IngestionPipeline
from app.ai.documents.schemas import DocumentChunk
from app.ai.vectorstores.pgvector import PgVectorStore
from app.core.config import Settings
from app.db.documents import SqlDocumentStore
from app.db.identity import SqlUserStore
from app.db.models import Document
from app.services.knowledge_service import KnowledgeService, KnowledgeServiceError

FIXTURES = Path(__file__).resolve().parent / "data" / "documents"
DIMENSIONS = 1536


class _FakeEmbeddingProvider:
    dimensions = DIMENSIONS

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [
            [float(index % DIMENSIONS), 0.0] + [0.0] * (DIMENSIONS - 2)
            for index, _ in enumerate(texts)
        ]


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
        sub=f"knowledge-{uuid.uuid4()}",
        email=None,
        name=None,
        picture=None,
    )
    return user.id


def _service(session) -> KnowledgeService:
    settings = Settings(openai_api_key="test-key")
    pipeline = IngestionPipeline(settings, embedding_provider=_FakeEmbeddingProvider())
    vector_store = PgVectorStore(session, settings)
    return KnowledgeService(
        session=session,
        settings=settings,
        pipeline=pipeline,
        vector_store=vector_store,
    )


@pytest.fixture
async def pgvector_session(db_session):
    if not await _pgvector_available(db_session):
        pytest.skip("pgvector extension not available — run alembic upgrade head")
    yield db_session


@pytest.mark.anyio
async def test_knowledge_service_ingest_persists_embeddings(pgvector_session) -> None:
    user_id = await _make_user(pgvector_session)
    service = _service(pgvector_session)
    file_bytes = (FIXTURES / "sample.txt").read_bytes()

    document_id = await service.ingest_document(
        user_id=user_id,
        file_bytes=file_bytes,
        filename="sample.txt",
        mime_type="text/plain",
    )

    document = await pgvector_session.scalar(
        select(Document).where(Document.id == document_id)
    )
    assert document is not None
    assert document.status == "ready"

    chunks = await SqlDocumentStore(pgvector_session).list_chunks(document_id)
    assert chunks
    assert all(chunk.embedding is not None for chunk in chunks)


@pytest.mark.anyio
async def test_knowledge_service_ingest_failure_sets_failed_and_cleans_up(
    pgvector_session,
) -> None:
    user_id = await _make_user(pgvector_session)
    settings = Settings(openai_api_key="test-key")
    pipeline = AsyncMock(spec=IngestionPipeline)
    pipeline.parse = AsyncMock(
        return_value=type("Parsed", (), {"text": "x", "metadata": {}})()
    )
    pipeline.chunk.return_value = [
        DocumentChunk(chunk_index=0, content="chunk", metadata={})
    ]
    pipeline.embed.side_effect = RuntimeError("embed failed")
    service = KnowledgeService(
        session=pgvector_session,
        settings=settings,
        pipeline=pipeline,
        vector_store=PgVectorStore(pgvector_session, settings),
    )

    with pytest.raises(RuntimeError, match="embed failed"):
        await service.ingest_document(
            user_id=user_id,
            file_bytes=(FIXTURES / "sample.txt").read_bytes(),
            filename="sample.txt",
            mime_type="text/plain",
        )

    document = await pgvector_session.scalar(
        select(Document)
        .where(Document.user_id == user_id)
        .order_by(Document.created_at.desc())
        .limit(1)
    )
    assert document is not None
    assert document.status == "failed"
    chunks = await SqlDocumentStore(pgvector_session).list_chunks(document.id)
    assert chunks == []


@pytest.mark.anyio
async def test_knowledge_service_delete_enforces_ownership(pgvector_session) -> None:
    owner_id = await _make_user(pgvector_session)
    other_id = await _make_user(pgvector_session)
    service = _service(pgvector_session)
    document_id = await service.ingest_document(
        user_id=owner_id,
        file_bytes=(FIXTURES / "sample.txt").read_bytes(),
        filename="sample.txt",
        mime_type="text/plain",
    )

    await service.delete_document(owner_id, document_id)

    with pytest.raises(KnowledgeServiceError) as exc_info:
        await service.delete_document(other_id, document_id)
    assert exc_info.value.code == "document_not_found"


def test_knowledge_service_has_no_search_method() -> None:
    assert not hasattr(KnowledgeService, "search")
    public_methods = [
        name
        for name, member in inspect.getmembers(
            KnowledgeService, predicate=inspect.isfunction
        )
        if not name.startswith("_")
    ]
    assert set(public_methods) == {"delete_document", "ingest_document"}


@pytest.mark.anyio
async def test_knowledge_service_logs_ingest_counters_not_content(
    pgvector_session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="app.services.knowledge_service")
    user_id = await _make_user(pgvector_session)
    service = _service(pgvector_session)
    secret = (FIXTURES / "sample.txt").read_text()

    await service.ingest_document(
        user_id=user_id,
        file_bytes=secret.encode(),
        filename="sample.txt",
        mime_type="text/plain",
    )

    success_records = [
        record
        for record in caplog.records
        if record.name == "app.services.knowledge_service"
        and getattr(record, "documents_ingested_total", None) == 1
    ]
    assert success_records
    assert secret not in caplog.text
