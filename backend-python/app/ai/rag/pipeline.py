"""Advanced retrieval pipeline protocol and Phase-1 skeleton.

The skeleton delegates to the V1 dense :class:`~app.ai.rag.retriever.Retriever`
until later phases fill rewrite → hybrid → filter → parent → rerank → compress
→ cite stages. Chat/RAG hot paths are not wired here (Phase 10).
"""

from __future__ import annotations

import time
from typing import Protocol

from app.ai.rag.retriever import Retriever
from app.ai.rag.schemas import (
    RetrievalRequest,
    RetrievalResult,
    RetrievedCandidate,
)


class AdvancedRetrievalPipeline(Protocol):
    """Orchestrator entry for flag-on advanced retrieval (stable after Phase 1)."""

    async def retrieve(self, request: RetrievalRequest) -> RetrievalResult: ...


class DefaultAdvancedRetrievalPipeline:
    """Skeleton pipeline that delegates dense retrieval to :class:`Retriever`.

    Later phases replace this body with the full advanced stage graph while
    keeping the :meth:`retrieve` contract stable.
    """

    def __init__(self, *, retriever: Retriever) -> None:
        self._retriever = retriever

    async def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        start = time.perf_counter()
        chunks = await self._retriever.retrieve(
            question=request.question,
            user_id=request.user_id,
            top_k=request.top_k,
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
        latency_ms = int((time.perf_counter() - start) * 1000)
        return RetrievalResult(
            candidates=candidates,
            citations=[],
            context_text="",
            truncated=False,
            retrieval_latency_ms=latency_ms,
        )
