"""Context compressor protocol for advanced RAG (public API — stable after Phase 1)."""

from __future__ import annotations

from typing import Protocol

from app.ai.rag.context_builder import BuiltContext
from app.ai.rag.schemas import RetrievedCandidate


class ContextCompressor(Protocol):
    """Select/trim/remove candidates to fit a character budget.

    Implementations must preserve original source text — never rewrite,
    summarize, or paraphrase document content.
    """

    def compress(
        self,
        candidates: list[RetrievedCandidate],
        *,
        max_chars: int,
    ) -> BuiltContext: ...
