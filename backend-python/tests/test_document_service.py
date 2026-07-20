"""Integration tests for DocumentService and document schema migration."""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import inspect, text

from app.ai.documents.pipeline import IngestionPipeline
from app.core.config import Settings
from app.db.documents import SqlDocumentStore
from app.db.identity import SqlUserStore
from app.services.document_service import DocumentService, DocumentServiceError

FIXTURES = Path(__file__).resolve().parent / "data" / "documents"


async def _make_user(session) -> uuid.UUID:
    user = await SqlUserStore(session).create(
        sub=f"doc-{uuid.uuid4()}",
        email=None,
        name=None,
        picture=None,
    )
    return user.id


def _service(session, settings: Settings | None = None) -> DocumentService:
    resolved = settings or Settings(
        document_upload_max_bytes=10_485_760,
        openai_api_key="test-key",
    )
    return DocumentService(session=session, settings=resolved)


@pytest.mark.anyio
async def test_document_service_ingest_persists_chunks_with_ownership(
    db_session,
) -> None:
    user_id = await _make_user(db_session)
    file_bytes = (FIXTURES / "sample.txt").read_bytes()
    service = _service(db_session)

    document_id = await service.ingest_document(
        user_id=user_id,
        file_bytes=file_bytes,
        filename="sample.txt",
        mime_type="text/plain",
    )

    document = await service.get_document(user_id, document_id)
    assert document is not None
    assert document.status == "ready"

    chunks = await SqlDocumentStore(db_session).list_chunks(document_id)
    assert chunks
    assert all(chunk.embedding is None for chunk in chunks)


@pytest.mark.anyio
async def test_document_service_rejects_file_too_large(db_session) -> None:
    user_id = await _make_user(db_session)
    settings = Settings(document_upload_max_bytes=32, openai_api_key="test-key")
    service = _service(db_session, settings)

    with pytest.raises(DocumentServiceError) as exc_info:
        await service.ingest_document(
            user_id=user_id,
            file_bytes=b"x" * 64,
            filename="large.txt",
            mime_type="text/plain",
        )

    assert exc_info.value.code == "document_too_large"


@pytest.mark.anyio
async def test_document_service_rejects_unsupported_type(db_session) -> None:
    user_id = await _make_user(db_session)
    service = _service(db_session)

    with pytest.raises(DocumentServiceError) as exc_info:
        await service.ingest_document(
            user_id=user_id,
            file_bytes=b"<html></html>",
            filename="page.html",
            mime_type="text/html",
        )

    assert exc_info.value.code == "unsupported_document_type"


@pytest.mark.anyio
async def test_document_service_enforces_ownership(db_session) -> None:
    owner_id = await _make_user(db_session)
    other_id = await _make_user(db_session)
    file_bytes = (FIXTURES / "sample.md").read_bytes()
    service = _service(db_session)

    document_id = await service.ingest_document(
        user_id=owner_id,
        file_bytes=file_bytes,
        filename="sample.md",
        mime_type="text/markdown",
    )

    assert await service.get_document(owner_id, document_id) is not None
    assert await service.get_document(other_id, document_id) is None


@pytest.mark.anyio
async def test_document_service_parse_failure_sets_failed_without_chunks(
    db_session,
) -> None:
    user_id = await _make_user(db_session)
    pipeline = AsyncMock(spec=IngestionPipeline)
    pipeline.parse.side_effect = RuntimeError("parse failed")
    settings = Settings(openai_api_key="test-key")
    service = DocumentService(session=db_session, settings=settings, pipeline=pipeline)

    with pytest.raises(RuntimeError, match="parse failed"):
        await service.ingest_document(
            user_id=user_id,
            file_bytes=(FIXTURES / "sample.txt").read_bytes(),
            filename="sample.txt",
            mime_type="text/plain",
        )

    from sqlalchemy import select

    from app.db.models import Document

    document = await db_session.scalar(
        select(Document)
        .where(Document.user_id == user_id)
        .order_by(Document.created_at.desc())
        .limit(1)
    )
    assert document is not None
    assert document.status == "failed"
    chunks = await SqlDocumentStore(db_session).list_chunks(document.id)
    assert chunks == []


@pytest.mark.anyio
async def test_document_tables_and_null_embedding_column(db_session) -> None:
    connection = await db_session.connection()
    tables = await connection.run_sync(
        lambda sync_conn: inspect(sync_conn).get_table_names()
    )
    assert "documents" in tables
    assert "document_chunks" in tables

    result = await db_session.execute(
        text(
            """
            SELECT column_name, is_nullable, udt_name
            FROM information_schema.columns
            WHERE table_name = 'document_chunks' AND column_name = 'embedding'
            """
        )
    )
    row = result.one()
    assert row.is_nullable == "YES"
