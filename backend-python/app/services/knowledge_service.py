"""Knowledge platform ingestion lifecycle (parse → chunk → embed → store)."""

from __future__ import annotations

import uuid
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.documents.pipeline import IngestionPipeline
from app.ai.interfaces.vector_store import VectorStore
from app.ai.rag.indexing import PendingIndexingWork, SyncIndexingRunner
from app.ai.rag.schemas import IndexingJobState
from app.core.config import Settings
from app.core.logging import get_logger
from app.db.documents import SqlDocumentStore
from app.db.models import Document
from app.services.document_service import validate_document_upload

_logger = get_logger(__name__)


class UploadQuotaChecker(Protocol):
    async def reserve_upload(self, user_id: uuid.UUID) -> None: ...

    async def release_upload(self, user_id: uuid.UUID) -> None: ...


class KnowledgeServiceError(Exception):
    """Ownership or lifecycle failure surfaced to callers."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class KnowledgeService:
    """Orchestrates full vector ingest and document deletion (no retrieval)."""

    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        pipeline: IngestionPipeline,
        vector_store: VectorStore,
        quota_service: UploadQuotaChecker | None = None,
        indexing_runner: SyncIndexingRunner | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._store = SqlDocumentStore(session)
        self._pipeline = pipeline
        self._vector_store = vector_store
        self._quota_service = quota_service
        # Thin IndexingJob hook: sync in-process runner (Epic 9 queue later).
        self._indexing = indexing_runner or SyncIndexingRunner(
            processor=self._run_indexing_work
        )

    async def ingest_document(
        self,
        user_id: uuid.UUID,
        file_bytes: bytes,
        filename: str,
        mime_type: str | None,
    ) -> uuid.UUID:
        validate_document_upload(
            self._settings,
            file_bytes=file_bytes,
            filename=filename,
            mime_type=mime_type,
        )

        quota_reserved = False
        if self._quota_service is not None:
            await self._quota_service.reserve_upload(user_id)
            quota_reserved = True

        document = await self._store.create_document(
            user_id=user_id,
            filename=filename,
            mime_type=mime_type,
            status="pending",
        )
        document_id = document.id

        self._indexing.register_pending_work(
            PendingIndexingWork(
                document_id=document_id,
                user_id=user_id,
                file_bytes=file_bytes,
                filename=filename,
                mime_type=mime_type,
            )
        )

        try:
            job_id = await self._indexing.submit(
                document_id=document_id,
                user_id=user_id,
            )
            status = await self._indexing.get_status(job_id)
            _logger.info(
                "Document ingested with embeddings",
                documents_ingested_total=1,
                document_id=str(document_id),
                indexing_job_id=job_id,
                indexing_job_status=status.state.value,
            )
            return document_id
        except Exception:
            if quota_reserved and self._quota_service is not None:
                await self._quota_service.release_upload(user_id)
            await self._cleanup_failed_ingest(document_id)
            failed_status = IndexingJobState.FAILED.value
            _logger.error(
                "Document ingestion failed",
                documents_failed_total=1,
                document_id=str(document_id),
                indexing_job_status=failed_status,
                exc_info=True,
            )
            raise

    async def _run_indexing_work(self, work: PendingIndexingWork) -> None:
        """Parse → chunk → embed → persist for a registered sync indexing job."""
        await self._store.set_status(work.document_id, "processing")
        parsed = await self._pipeline.parse(
            work.file_bytes,
            work.filename,
            work.mime_type,
        )
        chunks = self._pipeline.chunk(parsed)
        chunk_rows = [
            (chunk.chunk_index, chunk.content, chunk.metadata, chunk.id)
            for chunk in chunks
        ]
        await self._store.add_chunks(work.document_id, chunk_rows)
        # Parents are stored for expansion but not embedded by default.
        to_embed = [
            chunk for chunk in chunks if chunk.metadata.get("chunk_kind") != "parent"
        ]
        embedded = await self._pipeline.embed(to_embed)
        await self._pipeline.persist(
            document_id=work.document_id,
            user_id=work.user_id,
            chunks=embedded,
            vector_store=self._vector_store,
        )
        await self._store.set_status(work.document_id, "ready")
        await self._session.flush()

    async def list_documents(self, user_id: uuid.UUID) -> list[Document]:
        return await self._store.list_documents_for_user(user_id)

    async def get_document(
        self,
        user_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> Document:
        document = await self._store.get_owned_document(
            document_id,
            user_id=user_id,
        )
        if document is None:
            raise KnowledgeServiceError(
                code="document_not_found",
                message="Document not found.",
            )
        return document

    async def delete_document(
        self,
        user_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> None:
        document = await self._store.get_owned_document(
            document_id,
            user_id=user_id,
        )
        if document is None:
            raise KnowledgeServiceError(
                code="document_not_found",
                message="Document not found.",
            )
        await self._vector_store.delete_by_document(document_id)
        await self._store.delete_document(document_id)
        await self._session.flush()

    async def _cleanup_failed_ingest(self, document_id: uuid.UUID) -> None:
        await self._store.delete_chunks(document_id)
        await self._store.set_status(document_id, "failed")
        await self._session.flush()
