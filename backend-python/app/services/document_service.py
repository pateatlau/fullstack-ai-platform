"""Document ingestion service (auth-only ownership and status tracking)."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.documents.parsers.errors import UnsupportedDocumentTypeError
from app.ai.documents.parsers.router import is_supported_document_type
from app.ai.documents.pipeline import IngestionPipeline
from app.core.config import Settings
from app.db.documents import SqlDocumentStore
from app.db.models import Document


class DocumentServiceError(Exception):
    """Validation or ingestion failure surfaced to callers."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def validate_document_upload(
    settings: Settings,
    *,
    file_bytes: bytes,
    filename: str,
    mime_type: str | None,
) -> None:
    """Shared MIME/size validation for document ingest paths."""
    if len(file_bytes) > settings.document_upload_max_bytes:
        raise DocumentServiceError(
            code="document_too_large",
            message=(
                f"Document exceeds the {settings.document_upload_max_bytes} "
                "byte upload limit."
            ),
        )
    if is_supported_document_type(mime_type, filename):
        return
    try:
        from app.ai.documents.parsers.router import select_parser

        select_parser(mime_type, filename)
    except UnsupportedDocumentTypeError as exc:
        raise DocumentServiceError(
            code="unsupported_document_type",
            message=str(exc),
        ) from exc


class DocumentService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        pipeline: IngestionPipeline | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._store = SqlDocumentStore(session)
        self._pipeline = pipeline or IngestionPipeline(settings)

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
            await self._store.set_status(document_id, "ready")
            await self._session.flush()
            return document_id
        except Exception:
            await self._store.delete_chunks(document_id)
            await self._store.set_status(document_id, "failed")
            await self._session.flush()
            raise

    async def get_document(
        self,
        user_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> Document | None:
        return await self._store.get_owned_document(document_id, user_id=user_id)
