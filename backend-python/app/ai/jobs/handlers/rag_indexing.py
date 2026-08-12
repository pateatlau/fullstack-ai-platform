"""RAG queue-backed indexing job handler (Epic 10 Phase 5)."""

from __future__ import annotations

import uuid
from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.documents.pipeline import IngestionPipeline
from app.ai.embeddings.factory import create_embedding_provider
from app.ai.jobs.models import BackgroundJob, JobResult
from app.ai.jobs.retry import NonRetryableJobError
from app.ai.rag.indexing.queue_runner import _PAYLOAD_VERSION
from app.ai.rag.indexing.sync_runner import PendingIndexingWork
from app.ai.rag.indexing.work import cleanup_failed_indexing, run_indexing_work
from app.ai.vectorstores.pgvector import PgVectorStore
from app.core.config import Settings
from app.db.documents import SqlDocumentStore


def _parse_uuid(value: object, *, field: str) -> uuid.UUID:
    if not isinstance(value, str):
        raise NonRetryableJobError(f"invalid {field} in payload")
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise NonRetryableJobError(f"invalid {field} in payload") from exc


def build_rag_indexing_pipeline(settings: Settings) -> IngestionPipeline:
    """Construct the ingest pipeline used by the queue-backed handler."""
    return IngestionPipeline(
        settings,
        embedding_provider=create_embedding_provider(settings),
    )


async def rag_document_indexing(
    job: BackgroundJob,
    *,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    build_pipeline: Callable[
        [Settings], IngestionPipeline
    ] = build_rag_indexing_pipeline,
) -> JobResult:
    """Re-fetch staged upload bytes and run the standard indexing pipeline."""
    if not settings.background_jobs_enabled:
        return JobResult(summary="background jobs disabled")

    payload = job.payload
    version = payload.get("version")
    if version != _PAYLOAD_VERSION:
        raise NonRetryableJobError(f"unsupported payload version: {version!r}")

    document_id = _parse_uuid(payload.get("document_id"), field="document_id")
    user_id = _parse_uuid(payload.get("user_id"), field="user_id")

    async with session_factory() as session:
        store = SqlDocumentStore(session)
        document = await store.get_owned_document(document_id, user_id=user_id)
        if document is None:
            raise NonRetryableJobError("document not found")

        if document.status == "ready":
            await store.delete_upload_staging(document_id)
            await session.commit()
            return JobResult(
                summary="document already indexed",
                ref_id=str(document_id),
            )

        staged = await store.fetch_upload_staging(document_id)
        if staged is None:
            raise NonRetryableJobError("upload staging bytes not found")
        if staged.user_id != user_id:
            raise NonRetryableJobError("upload staging user mismatch")

        pipeline = build_pipeline(settings)
        vector_store = PgVectorStore(session, settings)
        work = PendingIndexingWork(
            document_id=document_id,
            user_id=user_id,
            file_bytes=staged.file_bytes,
            filename=staged.filename,
            mime_type=staged.mime_type,
        )

        try:
            await run_indexing_work(
                session=session,
                pipeline=pipeline,
                vector_store=vector_store,
                work=work,
            )
            await store.delete_upload_staging(document_id)
            await session.commit()
        except Exception as exc:
            await cleanup_failed_indexing(session, document_id)
            if isinstance(exc, NonRetryableJobError) or (
                job.attempt_count >= job.max_attempts
            ):
                await store.delete_upload_staging(document_id)
            await session.commit()
            raise

    return JobResult(summary="document indexed", ref_id=str(document_id))
