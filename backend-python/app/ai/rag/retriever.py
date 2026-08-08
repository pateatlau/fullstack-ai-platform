"""Domain-agnostic retrieval: embed query → vector search → ranked chunks."""

from __future__ import annotations

import time
import uuid

from app.ai.interfaces.embedding_provider import EmbeddingProvider
from app.ai.interfaces.vector_store import ScoredChunk, VectorStore
from app.ai.observability.tracing.spans import (
    elapsed_ms_since,
    rag_span,
    record_rag_span_outcome,
)
from app.ai.rag.metadata_filter import is_unsatisfiable_filter
from app.ai.rag.schemas import MetadataFilter
from app.core.config import Settings
from app.core.logging import get_logger

_logger = get_logger(__name__)


class Retriever:
    """Embed a user question and return owner-scoped similarity-ranked chunks."""

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
        top_k: int | None = None,
        filters: MetadataFilter | None = None,
    ) -> list[ScoredChunk]:
        effective_top_k = top_k if top_k is not None else self._settings.rag_top_k
        start = time.perf_counter()

        with rag_span("retrieve") as span:
            # Empty document_ids/tags frozensets can never match — skip embed/search.
            if filters is not None and is_unsatisfiable_filter(filters):
                latency_ms = elapsed_ms_since(start)
                record_rag_span_outcome(
                    span,
                    top_k=effective_top_k,
                    retrieved_count=0,
                    latency_ms=latency_ms,
                )
                _logger.info(
                    "Retrieval completed",
                    retrieval_latency_ms=latency_ms,
                    result_count=0,
                )
                return []

            embeddings = await self._embedding_provider.embed_texts([question])
            if not embeddings:
                latency_ms = elapsed_ms_since(start)
                record_rag_span_outcome(
                    span,
                    top_k=effective_top_k,
                    retrieved_count=0,
                    latency_ms=latency_ms,
                )
                _logger.info(
                    "Retrieval completed",
                    retrieval_latency_ms=latency_ms,
                    result_count=0,
                )
                return []

            results = await self._vector_store.similarity_search(
                embeddings[0],
                top_k=effective_top_k,
                user_id=user_id,
                filters=filters,
            )

            latency_ms = elapsed_ms_since(start)
            record_rag_span_outcome(
                span,
                top_k=effective_top_k,
                retrieved_count=len(results),
                latency_ms=latency_ms,
            )
            _logger.info(
                "Retrieval completed",
                retrieval_latency_ms=latency_ms,
                result_count=len(results),
            )
            return results
