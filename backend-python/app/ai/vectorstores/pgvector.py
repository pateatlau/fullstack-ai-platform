"""pgvector-backed vector store (V1 single concrete implementation)."""

from __future__ import annotations

import time
import uuid
from typing import Any, cast

from sqlalchemy import ColumnElement, delete, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.documents.schemas import DocumentChunk
from app.ai.interfaces.vector_store import ScoredChunk
from app.ai.rag.metadata_filter import is_unsatisfiable_filter
from app.ai.rag.schemas import MetadataFilter
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
        filters: MetadataFilter | None = None,
    ) -> list[ScoredChunk]:
        if top_k < 1:
            return []
        if filters is not None and is_unsatisfiable_filter(filters):
            return []

        start = time.perf_counter()
        distance = DocumentChunkRow.embedding.cosine_distance(query_embedding)
        stmt = (
            select(
                DocumentChunkRow,
                (1 - distance).label("score"),
                Document.mime_type,
            )
            .join(Document, DocumentChunkRow.document_id == Document.id)
            .where(
                Document.user_id == user_id,
                DocumentChunkRow.embedding.is_not(None),
                *_metadata_filter_clauses(filters),
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
                metadata=_scored_metadata(chunk_row.metadata_json, mime_type),
                score=float(score),
            )
            for chunk_row, score, mime_type in rows
        ]

    async def delete_by_document(self, document_id: uuid.UUID) -> None:
        await self._session.execute(
            delete(DocumentChunkRow).where(DocumentChunkRow.document_id == document_id)
        )
        await self._session.flush()


def _metadata_filter_clauses(
    filters: MetadataFilter | None,
) -> list[ColumnElement[bool]]:
    """Build AND predicates for optional metadata / document filters."""
    if filters is None:
        return []

    clauses: list[ColumnElement[bool]] = []
    if filters.document_ids is not None:
        clauses.append(DocumentChunkRow.document_id.in_(filters.document_ids))
    if filters.tags is not None:
        # JSONB @> — chunk tags array must contain every requested tag (AND).
        clauses.append(
            DocumentChunkRow.metadata_json.contains({"tags": sorted(filters.tags)})
        )
    if filters.source is not None:
        clauses.append(
            DocumentChunkRow.metadata_json["source"].as_string() == filters.source
        )
    if filters.mime_type is not None:
        clauses.append(Document.mime_type == filters.mime_type)
    return clauses


def _scored_metadata(
    metadata_json: dict[str, object],
    mime_type: str | None,
) -> dict[str, object]:
    """Copy chunk metadata; document mime_type overwrites any chunk-level value."""
    metadata = dict(metadata_json)
    if mime_type is not None:
        metadata["mime_type"] = mime_type
    return metadata
