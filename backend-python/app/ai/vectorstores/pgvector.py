"""pgvector-backed vector store (V1 single concrete implementation)."""

from __future__ import annotations

import time
import uuid
from typing import Any, cast

from sqlalchemy import delete, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.documents.schemas import DocumentChunk
from app.ai.interfaces.vector_store import ScoredChunk
from app.core.config import Settings
from app.core.logging import get_logger
from app.db.models import Document, DocumentChunk as DocumentChunkRow

_logger = get_logger(__name__)


class PgVectorStore:
    """Store and query embeddings in Postgres via the pgvector extension."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def upsert(
        self,
        *,
        document_id: uuid.UUID,
        user_id: uuid.UUID,
        chunks: list[DocumentChunk],
    ) -> None:
        owned = await self._session.scalar(
            select(Document.id).where(
                Document.id == document_id,
                Document.user_id == user_id,
            )
        )
        if owned is None:
            raise ValueError(f"Document {document_id} not found for user {user_id}.")

        for chunk in chunks:
            if chunk.embedding is None:
                raise ValueError(
                    f"Chunk {chunk.chunk_index} has no embedding to persist."
                )
            if len(chunk.embedding) != self._settings.embedding_dimensions:
                raise ValueError(
                    f"Embedding dimension {len(chunk.embedding)} does not match "
                    f"configured {self._settings.embedding_dimensions}."
                )
            result = await self._session.execute(
                update(DocumentChunkRow)
                .where(
                    DocumentChunkRow.document_id == document_id,
                    DocumentChunkRow.chunk_index == chunk.chunk_index,
                )
                .values(embedding=chunk.embedding)
            )
            if cast(CursorResult[Any], result).rowcount == 0:
                raise ValueError(
                    f"Chunk index {chunk.chunk_index} not found for document "
                    f"{document_id}."
                )

        await self._session.flush()

    async def similarity_search(
        self,
        query_embedding: list[float],
        *,
        top_k: int,
        user_id: uuid.UUID,
    ) -> list[ScoredChunk]:
        if top_k < 1:
            return []

        start = time.perf_counter()
        distance = DocumentChunkRow.embedding.cosine_distance(query_embedding)
        stmt = (
            select(DocumentChunkRow, (1 - distance).label("score"))
            .join(Document, DocumentChunkRow.document_id == Document.id)
            .where(
                Document.user_id == user_id,
                DocumentChunkRow.embedding.is_not(None),
            )
            .order_by(distance)
            .limit(top_k)
        )
        rows = (await self._session.execute(stmt)).all()
        latency_ms = int((time.perf_counter() - start) * 1000)
        _logger.info(
            "Vector similarity search completed",
            vector_search_latency_ms=latency_ms,
            result_count=len(rows),
        )

        return [
            ScoredChunk(
                chunk_id=chunk_row.id,
                document_id=chunk_row.document_id,
                chunk_index=chunk_row.chunk_index,
                content=chunk_row.content,
                metadata=dict(chunk_row.metadata_json),
                score=float(score),
            )
            for chunk_row, score in rows
        ]

    async def delete_by_document(self, document_id: uuid.UUID) -> None:
        await self._session.execute(
            delete(DocumentChunkRow).where(DocumentChunkRow.document_id == document_id)
        )
        await self._session.flush()
