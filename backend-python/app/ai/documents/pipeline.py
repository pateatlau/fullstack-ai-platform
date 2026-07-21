"""Parse, chunk, and in-memory embed orchestration."""

from __future__ import annotations

import time
import uuid
from dataclasses import replace

from app.ai.documents.chunkers.recursive import RecursiveChunker
from app.ai.documents.parsers.router import select_parser
from app.ai.documents.schemas import DocumentChunk, ParsedDocument
from app.ai.interfaces.embedding_provider import EmbeddingProvider
from app.ai.interfaces.vector_store import VectorStore
from app.core.config import Settings
from app.core.logging import get_logger

_logger = get_logger(__name__)


class IngestionPipeline:
    """Orchestrates parse → chunk → embed → optional vector persist."""

    def __init__(
        self,
        settings: Settings,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self._settings = settings
        self._chunker = RecursiveChunker(settings)
        self._embedding_provider = embedding_provider

    async def parse(
        self,
        file_bytes: bytes,
        filename: str,
        mime_type: str | None,
    ) -> ParsedDocument:
        parser = select_parser(mime_type, filename)
        return await parser.parse(file_bytes, filename)

    def chunk(self, document: ParsedDocument) -> list[DocumentChunk]:
        return self._chunker.chunk(document)

    async def embed(self, chunks: list[DocumentChunk]) -> list[DocumentChunk]:
        if self._embedding_provider is None:
            raise RuntimeError(
                "Embedding provider is not configured for this ingestion pipeline."
            )
        if not chunks:
            return []

        texts = [chunk.content for chunk in chunks]
        start = time.perf_counter()
        vectors = await self._embedding_provider.embed_texts(texts)
        latency_ms = int((time.perf_counter() - start) * 1000)
        _logger.info(
            "Document chunks embedded",
            embedding_latency_ms=latency_ms,
            text_count=len(texts),
        )

        if len(vectors) != len(chunks):
            raise ValueError(
                f"Embedding provider returned {len(vectors)} vectors for "
                f"{len(chunks)} chunks."
            )

        return [
            replace(chunk, embedding=vector)
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]

    async def parse_chunk_embed(
        self,
        file_bytes: bytes,
        filename: str,
        mime_type: str | None,
    ) -> list[DocumentChunk]:
        parsed = await self.parse(file_bytes, filename, mime_type)
        chunks = self.chunk(parsed)
        return await self.embed(chunks)

    async def persist(
        self,
        *,
        document_id: uuid.UUID,
        user_id: uuid.UUID,
        chunks: list[DocumentChunk],
        vector_store: VectorStore,
    ) -> None:
        if any(chunk.embedding is None for chunk in chunks):
            raise ValueError("All chunks must have embeddings before persist.")
        await vector_store.upsert(
            document_id=document_id,
            user_id=user_id,
            chunks=chunks,
        )

    async def parse_chunk_embed_persist(
        self,
        *,
        file_bytes: bytes,
        filename: str,
        mime_type: str | None,
        document_id: uuid.UUID,
        user_id: uuid.UUID,
        vector_store: VectorStore,
    ) -> list[DocumentChunk]:
        chunks = await self.parse_chunk_embed(file_bytes, filename, mime_type)
        await self.persist(
            document_id=document_id,
            user_id=user_id,
            chunks=chunks,
            vector_store=vector_store,
        )
        return chunks
