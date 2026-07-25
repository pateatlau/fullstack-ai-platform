"""Reranker protocol for advanced RAG (public API — stable after Phase 1)."""

from __future__ import annotations

from typing import Protocol

from app.ai.rag.schemas import RetrievedCandidate


class Reranker(Protocol):
    """Cross-encoder (or equivalent) rerank over retrieved candidates."""

    async def rerank(
        self,
        query: str,
        candidates: list[RetrievedCandidate],
        *,
        top_n: int,
    ) -> list[RetrievedCandidate]: ...
