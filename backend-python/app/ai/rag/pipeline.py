"""Advanced retrieval pipeline protocol and default orchestrator.

Flag-on stage graph (Part I): rewrite → hybrid → filter → parent expand →
rerank → compress → cite. Chat/RAG hot paths branch to this pipeline when
``ADVANCED_RAG_ENABLED`` (Phase 10). Flag-off callers keep V1 dense
``Retriever`` → ``ContextBuilder``.

Query rewrite runs at most once when gated on. Rerank failures keep
pre-rerank order / ``final_score``. Compress preserves original source text.
Citations are assigned after compression with contiguous ``[1..n]``.
"""

from __future__ import annotations

import time
from typing import Protocol

from app.ai.interfaces.context_compressor import ContextCompressor
from app.ai.interfaces.query_rewriter import QueryRewriter
from app.ai.interfaces.reranker import Reranker
from app.ai.rag.citations import CitationBuilder
from app.ai.rag.context_builder import BuiltContext
from app.ai.rag.hybrid.retriever import HybridRetriever
from app.ai.rag.metadata_filter import (
    apply_metadata_filter,
    is_unsatisfiable_filter,
)
from app.ai.rag.parent_expand import ParentContentFetcher, expand_parents
from app.ai.rag.retriever import Retriever
from app.ai.rag.schemas import (
    Citation,
    RetrievalRequest,
    RetrievalResult,
    RetrievedCandidate,
)
from app.core.config import Settings
from app.core.logging import get_logger

_logger = get_logger(__name__)


class AdvancedRetrievalPipeline(Protocol):
    """Orchestrator entry for flag-on advanced retrieval (stable after Phase 1)."""

    async def retrieve(self, request: RetrievalRequest) -> RetrievalResult: ...


class DefaultAdvancedRetrievalPipeline:
    """Advanced stage graph for flag-on retrieval.

    Prefer :class:`HybridRetriever` when provided (production DI). Dense
    :class:`Retriever` remains as a test / fallback path when hybrid is absent.
    Parent expansion runs when a :class:`ParentContentFetcher` is provided.
    """

    def __init__(
        self,
        *,
        retriever: Retriever | None = None,
        hybrid_retriever: HybridRetriever | None = None,
        parent_content_fetcher: ParentContentFetcher | None = None,
        query_rewriter: QueryRewriter | None = None,
        reranker: Reranker | None = None,
        context_compressor: ContextCompressor | None = None,
        citation_builder: CitationBuilder | None = None,
        settings: Settings | None = None,
    ) -> None:
        if hybrid_retriever is None and retriever is None:
            raise ValueError(
                "DefaultAdvancedRetrievalPipeline requires hybrid_retriever "
                "or retriever."
            )
        self._retriever = retriever
        self._hybrid_retriever = hybrid_retriever
        self._parent_content_fetcher = parent_content_fetcher
        self._query_rewriter = query_rewriter
        self._reranker = reranker
        self._context_compressor = context_compressor
        self._citation_builder = citation_builder
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
        candidates = await self._retrieve_candidates(search_query, request)
        # Part I stage 3 — filter candidates in place (AND with store push-down).
        candidates = apply_metadata_filter(candidates, request.filters)
        # Part I stage 4 — parent expand + dedupe (one block per parent_id).
        candidates = await self._maybe_expand_parents(candidates)
        # Part I stage 5 — cross-encoder rerank (Protocol); failures keep order.
        candidates = await self._maybe_rerank(search_query, candidates, request)
        # Part I stage 6 — faithful compress into context budget.
        built = self._maybe_compress(candidates)
        # Part I stage 7 — citations after compression, before prompt construction.
        citations = self._maybe_cite(built, candidates)
        latency_ms = int((time.perf_counter() - start) * 1000)
        _logger.info(
            "Advanced retrieval completed",
            advanced_rag_enabled=True,
            retrieval_latency_ms=latency_ms,
            candidate_count=len(candidates),
            citation_count=len(citations),
            truncated=built.truncated if built is not None else False,
        )
        return RetrievalResult(
            candidates=candidates,
            citations=citations,
            context_text=built.text if built is not None else "",
            truncated=built.truncated if built is not None else False,
            retrieval_latency_ms=latency_ms,
        )

    async def _retrieve_candidates(
        self,
        search_query: str,
        request: RetrievalRequest,
    ) -> list[RetrievedCandidate]:
        hybrid = self._hybrid_retriever
        if hybrid is not None:
            return await hybrid.retrieve(
                question=search_query,
                user_id=request.user_id,
                filters=request.filters,
            )

        assert self._retriever is not None
        chunks = await self._retriever.retrieve(
            question=search_query,
            user_id=request.user_id,
            top_k=request.top_k,
            filters=request.filters,
        )
        return [
            RetrievedCandidate(
                chunk=chunk,
                parent=None,
                metadata=dict(chunk.metadata),
                final_score=chunk.score,
                dense_score=chunk.score,
            )
            for chunk in chunks
        ]

    async def _maybe_expand_parents(
        self,
        candidates: list[RetrievedCandidate],
    ) -> list[RetrievedCandidate]:
        fetcher = self._parent_content_fetcher
        settings = self._settings
        if (
            fetcher is None
            or settings is None
            or not settings.advanced_rag_enabled
            or not candidates
        ):
            return candidates
        return await expand_parents(candidates, fetch_parent_contents=fetcher)

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

    async def _maybe_rerank(
        self,
        search_query: str,
        candidates: list[RetrievedCandidate],
        request: RetrievalRequest,
    ) -> list[RetrievedCandidate]:
        settings = self._settings
        reranker = self._reranker
        if (
            settings is None
            or reranker is None
            or not settings.advanced_rag_enabled
            or not candidates
        ):
            return candidates

        top_n = self._rerank_top_n(request, settings)
        try:
            return await reranker.rerank(
                search_query,
                candidates,
                top_n=top_n,
            )
        except Exception:
            # Safety net: Protocol impls should fall back themselves (Cohere does).
            _logger.warning(
                "Rerank raised; keeping pre-rerank order",
                rerank_failed=True,
                rerank_failure_reason="exception",
            )
            return candidates

    def _maybe_compress(
        self,
        candidates: list[RetrievedCandidate],
    ) -> BuiltContext | None:
        settings = self._settings
        compressor = self._context_compressor
        if settings is None or compressor is None or not settings.advanced_rag_enabled:
            return None

        return compressor.compress(
            candidates,
            max_chars=settings.rag_context_max_chars,
        )

    def _maybe_cite(
        self,
        built: BuiltContext | None,
        candidates: list[RetrievedCandidate],
    ) -> list[Citation]:
        settings = self._settings
        if settings is None or not settings.advanced_rag_enabled or built is None:
            return []

        builder = self._citation_builder or CitationBuilder(settings=settings)
        return builder.build(built.included_chunks, candidates=candidates)

    @staticmethod
    def _rerank_top_n(request: RetrievalRequest, settings: Settings) -> int:
        # Part I: rerank_top_n defaults to rag_top_k (default 5).
        if request.top_k is not None:
            return request.top_k
        return settings.rag_top_k
