"""SQLAlchemy-backed document persistence (Phase 5)."""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Document, DocumentChunk


class SqlDocumentStore:
    """Focused store for document ingestion and ownership queries."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_document(
        self,
        *,
        user_id: uuid.UUID,
        filename: str,
        mime_type: str | None,
        status: str = "pending",
    ) -> Document:
        document = Document(
            user_id=user_id,
            filename=filename,
            mime_type=mime_type,
            status=status,
        )
        self._session.add(document)
        await self._session.flush()
        return document

    async def set_status(self, document_id: uuid.UUID, status: str) -> None:
        await self._session.execute(
            update(Document).where(Document.id == document_id).values(status=status)
        )

    async def add_chunks(
        self,
        document_id: uuid.UUID,
        chunks: list[tuple[int, str, dict[str, object]]],
    ) -> None:
        for chunk_index, content, metadata in chunks:
            self._session.add(
                DocumentChunk(
                    document_id=document_id,
                    chunk_index=chunk_index,
                    content=content,
                    metadata_json=metadata,
                )
            )
        await self._session.flush()

    async def delete_chunks(self, document_id: uuid.UUID) -> None:
        await self._session.execute(
            delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
        )

    async def get_owned_document(
        self,
        document_id: uuid.UUID,
        *,
        user_id: uuid.UUID,
    ) -> Document | None:
        return await self._session.scalar(
            select(Document).where(
                Document.id == document_id,
                Document.user_id == user_id,
            )
        )

    async def list_chunks(self, document_id: uuid.UUID) -> list[DocumentChunk]:
        result = await self._session.scalars(
            select(DocumentChunk)
            .where(DocumentChunk.document_id == document_id)
            .order_by(DocumentChunk.chunk_index)
        )
        return list(result.all())
