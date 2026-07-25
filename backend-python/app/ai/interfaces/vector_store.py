"""Vector store protocol for embedding persistence and similarity search."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from app.ai.documents.schemas import DocumentChunk

if TYPE_CHECKING:
    from app.ai.rag.schemas import MetadataFilter


@dataclass(frozen=True)
class ScoredChunk:
    """A retrieved chunk with cosine similarity score (higher = more similar)."""

    chunk_id: uuid.UUID
    document_id: uuid.UUID
    chunk_index: int
    content: str
    metadata: dict[str, object]
    score: float


class VectorStore(Protocol):
    """Persist and query document embeddings scoped to authenticated users."""

    async def upsert(
        self,
        *,
        document_id: uuid.UUID,
        user_id: uuid.UUID,
        chunks: list[DocumentChunk],
    ) -> None: ...

    async def similarity_search(
        self,
        query_embedding: list[float],
        *,
        top_k: int,
        user_id: uuid.UUID,
        filters: MetadataFilter | None = None,
    ) -> list[ScoredChunk]: ...

    async def lexical_search(
        self,
        query: str,
        *,
        top_k: int,
        user_id: uuid.UUID,
        filters: MetadataFilter | None = None,
    ) -> list[ScoredChunk]: ...

    async def delete_by_document(self, document_id: uuid.UUID) -> None: ...
