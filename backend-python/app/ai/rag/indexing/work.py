"""Shared document indexing execution (parse → chunk → embed → store)."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.documents.pipeline import IngestionPipeline
from app.ai.interfaces.vector_store import VectorStore
from app.ai.rag.indexing.sync_runner import PendingIndexingWork
from app.db.documents import SqlDocumentStore


async def run_indexing_work(
    *,
    session: AsyncSession,
    pipeline: IngestionPipeline,
    vector_store: VectorStore,
    work: PendingIndexingWork,
) -> None:
    """Parse → chunk → embed → persist for one indexing job."""
    store = SqlDocumentStore(session)
    await store.set_status(work.document_id, "processing")
    parsed = await pipeline.parse(
        work.file_bytes,
        work.filename,
        work.mime_type,
    )
    chunks = pipeline.chunk(parsed)
    chunk_rows = [
        (chunk.chunk_index, chunk.content, chunk.metadata, chunk.id) for chunk in chunks
    ]
    await store.add_chunks(work.document_id, chunk_rows)
    to_embed = [
        chunk for chunk in chunks if chunk.metadata.get("chunk_kind") != "parent"
    ]
    embedded = await pipeline.embed(to_embed)
    await pipeline.persist(
        document_id=work.document_id,
        user_id=work.user_id,
        chunks=embedded,
        vector_store=vector_store,
    )
    await store.set_status(work.document_id, "ready")
    await session.flush()


async def cleanup_failed_indexing(
    session: AsyncSession,
    document_id: uuid.UUID,
    *,
    rollback_first: bool = True,
) -> None:
    """Remove partial chunks and mark the document failed."""
    if rollback_first:
        await session.rollback()
    store = SqlDocumentStore(session)
    await store.delete_chunks(document_id)
    await store.set_status(document_id, "failed")
    await session.flush()
