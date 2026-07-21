"""Knowledge platform ingestion lifecycle (parse → chunk → embed → store)."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.documents.pipeline import IngestionPipeline
from app.ai.interfaces.vector_store import VectorStore
from app.core.config import Settings
from app.core.logging import get_logger
from app.db.documents import SqlDocumentStore
from app.services.document_service import validate_document_upload

_logger = get_logger(__name__)


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
    ) -> None:
        self._session = session
        self._settings = settings
        self._store = SqlDocumentStore(session)
        self._pipeline = pipeline
        self._vector_store = vector_store

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

        document = await self._store.create_document(
            user_id=user_id,
            filename=filename,
            mime_type=mime_type,
            status="pending",
        )
        document_id = document.id

        try:
            await self._store.set_status(document_id, "processing")
            parsed = await self._pipeline.parse(file_bytes, filename, mime_type)
            chunks = self._pipeline.chunk(parsed)
            chunk_rows = [
                (chunk.chunk_index, chunk.content, chunk.metadata) for chunk in chunks
            ]
            await self._store.add_chunks(document_id, chunk_rows)
            embedded = await self._pipeline.embed(chunks)
            await self._pipeline.persist(
                document_id=document_id,
                user_id=user_id,
                chunks=embedded,
                vector_store=self._vector_store,
            )
            await self._store.set_status(document_id, "ready")
            await self._session.flush()
            _logger.info(
                "Document ingested with embeddings",
                documents_ingested_total=1,
                document_id=str(document_id),
            )
            return document_id
        except Exception:
            await self._cleanup_failed_ingest(document_id)
            _logger.error(
                "Document ingestion failed",
                documents_failed_total=1,
                document_id=str(document_id),
                exc_info=True,
            )
            raise

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
                message="Document not found or access denied.",
            )
        await self._store.delete_document(document_id)
        await self._session.flush()

    async def _cleanup_failed_ingest(self, document_id: uuid.UUID) -> None:
        await self._store.delete_chunks(document_id)
        await self._store.set_status(document_id, "failed")
        await self._session.flush()
