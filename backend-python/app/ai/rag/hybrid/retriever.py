"""Hybrid dense + lexical retriever fused with Reciprocal Rank Fusion."""

from __future__ import annotations

import asyncio
import time
import uuid

from app.ai.interfaces.embedding_provider import EmbeddingProvider
from app.ai.interfaces.vector_store import ScoredChunk, VectorStore
from app.ai.rag.hybrid.fusion import reciprocal_rank_fusion
from app.ai.rag.metadata_filter import is_unsatisfiable_filter
from app.ai.rag.schemas import MetadataFilter, RetrievedCandidate
from app.core.config import Settings
from app.core.logging import get_logger

_logger = get_logger(__name__)


class HybridRetriever:
    """Embed + dense search, Postgres FTS, then RRF → ``RetrievedCandidate``s.

    Not wired into chat/RAG hot paths until Phase 10. Flag-off callers continue
    to use dense-only :class:`~app.ai.rag.retriever.Retriever`.
    """

    def __init__(
        self,
        *,
        embedding_provider: EmbeddingProvider,
        vector_store: VectorStore,
        settings: Settings,
    ) -> None:
        self._embedding_provider = embedding_provider
        self._vector_store = vector_store
        self._settings = settings

    async def retrieve(
        self,
        *,
        question: str,
        user_id: uuid.UUID,
        filters: MetadataFilter | None = None,
        dense_top_k: int | None = None,
        lexical_top_k: int | None = None,
        rrf_k: int | None = None,
    ) -> list[RetrievedCandidate]:
        effective_dense_k = (
            dense_top_k
            if dense_top_k is not None
            else self._settings.hybrid_dense_top_k
        )
        effective_lexical_k = (
            lexical_top_k
            if lexical_top_k is not None
            else self._settings.hybrid_lexical_top_k
        )
        effective_rrf_k = rrf_k if rrf_k is not None else self._settings.rrf_k
        start = time.perf_counter()

        if filters is not None and is_unsatisfiable_filter(filters):
            _log_hybrid_complete(
                start,
                dense_count=0,
                lexical_count=0,
                rrf_count=0,
            )
            return []

        dense_chunks, lexical_chunks = await asyncio.gather(
            self._dense_search(
                question,
                user_id=user_id,
                top_k=effective_dense_k,
                filters=filters,
            ),
            self._vector_store.lexical_search(
                question,
                top_k=effective_lexical_k,
                user_id=user_id,
                filters=filters,
            ),
        )

        dense_by_id = {chunk.chunk_id: chunk for chunk in dense_chunks}
        lexical_by_id = {chunk.chunk_id: chunk for chunk in lexical_chunks}

        fused = reciprocal_rank_fusion(
            [dense_chunks, lexical_chunks],
            key=lambda chunk: chunk.chunk_id,
            rrf_k=effective_rrf_k,
        )

        candidates: list[RetrievedCandidate] = []
        for representative, rrf_score in fused:
            chunk_id = representative.chunk_id
            dense_chunk = dense_by_id.get(chunk_id)
            lexical_chunk = lexical_by_id.get(chunk_id)
            # Prefer dense ScoredChunk when both channels hit the same id.
            chunk = dense_chunk if dense_chunk is not None else representative
            candidates.append(
                RetrievedCandidate(
                    chunk=chunk,
                    parent=None,
                    metadata=dict(chunk.metadata),
                    final_score=rrf_score,
                    dense_score=(
                        float(dense_chunk.score) if dense_chunk is not None else None
                    ),
                    lexical_score=(
                        float(lexical_chunk.score)
                        if lexical_chunk is not None
                        else None
                    ),
                    rrf_score=rrf_score,
                )
            )

        _log_hybrid_complete(
            start,
            dense_count=len(dense_chunks),
            lexical_count=len(lexical_chunks),
            rrf_count=len(candidates),
        )
        return candidates

    async def _dense_search(
        self,
        question: str,
        *,
        user_id: uuid.UUID,
        top_k: int,
        filters: MetadataFilter | None,
    ) -> list[ScoredChunk]:
        if top_k < 1 or not question.strip():
            return []
        embeddings = await self._embedding_provider.embed_texts([question])
        if not embeddings:
            return []
        return await self._vector_store.similarity_search(
            embeddings[0],
            top_k=top_k,
            user_id=user_id,
            filters=filters,
        )


def _log_hybrid_complete(
    start: float,
    *,
    dense_count: int,
    lexical_count: int,
    rrf_count: int,
) -> None:
    latency_ms = int((time.perf_counter() - start) * 1000)
    _logger.info(
        "Hybrid retrieval completed",
        retrieval_latency_ms=latency_ms,
        hybrid_dense_count=dense_count,
        hybrid_lexical_count=lexical_count,
        rrf_result_count=rrf_count,
    )
