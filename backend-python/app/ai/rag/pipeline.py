"""Advanced retrieval pipeline protocol and skeleton.

The skeleton delegates to the V1 dense :class:`~app.ai.rag.retriever.Retriever`
until later phases fill hybrid → filter → parent → rerank → compress → cite
stages. Phase 5 adds optional query rewrite (at most once per request).
Chat/RAG hot paths are not wired here (Phase 10).

Phase 3: metadata filters are pushed down through :class:`Retriever` and
re-applied as Part I stage 3 over ``RetrievedCandidate``s.
"""

from __future__ import annotations

import time
from typing import Protocol

from app.ai.interfaces.query_rewriter import QueryRewriter
from app.ai.rag.metadata_filter import (
    apply_metadata_filter,
    is_unsatisfiable_filter,
)
from app.ai.rag.retriever import Retriever
from app.ai.rag.schemas import (
    RetrievalRequest,
    RetrievalResult,
    RetrievedCandidate,
)
from app.core.config import Settings


class AdvancedRetrievalPipeline(Protocol):
    """Orchestrator entry for flag-on advanced retrieval (stable after Phase 1)."""

    async def retrieve(self, request: RetrievalRequest) -> RetrievalResult: ...


class DefaultAdvancedRetrievalPipeline:
    """Skeleton pipeline that delegates dense retrieval to :class:`Retriever`.

    Later phases replace this body with the full advanced stage graph while
    keeping the :meth:`retrieve` contract stable.

    Query rewrite (Phase 5) runs at most once when ``advanced_rag_enabled``,
    ``query_rewrite_enabled``, and a :class:`QueryRewriter` are all set. The
    rewritten string is never fed back into the rewriter.
    """

    def __init__(
        self,
        *,
        retriever: Retriever,
        query_rewriter: QueryRewriter | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._retriever = retriever
        self._query_rewriter = query_rewriter
        self._settings = settings

    async def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        start = time.perf_counter()

        # Empty document_ids/tags frozensets → empty result (not an error).
        if request.filters is not None and is_unsatisfiable_filter(request.filters):
            latency_ms = int((time.perf_counter() - start) * 1000)
            return RetrievalResult(
                candidates=[],
                citations=[],
                context_text="",
                truncated=False,
                retrieval_latency_ms=latency_ms,
            )

        search_query = await self._resolve_search_query(request)

        chunks = await self._retriever.retrieve(
            question=search_query,
            user_id=request.user_id,
            top_k=request.top_k,
            filters=request.filters,
        )
        candidates = [
            RetrievedCandidate(
                chunk=chunk,
                parent=None,
                metadata=dict(chunk.metadata),
                final_score=chunk.score,
                dense_score=chunk.score,
            )
            for chunk in chunks
        ]
        # Part I stage 3 — filter candidates in place (AND with store push-down).
        candidates = apply_metadata_filter(candidates, request.filters)
        latency_ms = int((time.perf_counter() - start) * 1000)
        return RetrievalResult(
            candidates=candidates,
            citations=[],
            context_text="",
            truncated=False,
            retrieval_latency_ms=latency_ms,
        )

    async def _resolve_search_query(self, request: RetrievalRequest) -> str:
        """Return the retrieval query; rewrite at most once when gated on."""
        settings = self._settings
        rewriter = self._query_rewriter
        if (
            settings is None
            or rewriter is None
            or not settings.advanced_rag_enabled
            or not settings.query_rewrite_enabled
        ):
            return request.question

        # Single rewrite attempt — never pass the result back into rewrite().
        return await rewriter.rewrite(
            request.question,
            user_id=request.user_id,
        )
