"""Typed response envelopes for generic RAG orchestration."""

from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class RetrievedChunkMeta:
    """Metadata for chunks included in the LLM context (debugging only)."""

    chunk_id: uuid.UUID | None
    document_id: uuid.UUID | None
    chunk_index: int | None
    score: float


@dataclass(frozen=True)
class RAGResponse:
    """End-to-end RAG result for callers (HTTP layer maps this in Phase 11)."""

    answer: str
    retrieved_chunks: list[RetrievedChunkMeta]
    truncated: bool
    model: str
    provider: str
    retrieval_latency_ms: int | None = None
    llm_latency_ms: int | None = None
