"""Typed envelopes for generic RAG orchestration and advanced retrieval.

``RetrievedCandidate`` and related advanced models are the frozen internal
pipeline contract (stable after Epic 02 Phase 1). They are not HTTP DTOs.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

from app.ai.interfaces.vector_store import ScoredChunk


@dataclass(frozen=True)
class RetrievedChunkMeta:
    """Metadata for chunks included in the LLM context (debugging only)."""

    chunk_id: uuid.UUID | None
    document_id: uuid.UUID | None
    chunk_index: int | None
    score: float


@dataclass(frozen=True)
class Citation:
    """Structured citation for an included context block (post-compression)."""

    index: int
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    snippet: str
    score: float
    filename: str | None = None
    source: str | None = None
    page: int | str | None = None


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
    # Additive (Phase 8). ``None`` on V1 / flag-off path; list when advanced
    # retrieval produced citations (empty list = advanced ran, nothing included).
    citations: list[Citation] | None = None


@dataclass(frozen=True)
class MetadataFilter:
    """Structured filters applied to chunk/document metadata at retrieve time."""

    document_ids: frozenset[uuid.UUID] | None = None
    tags: frozenset[str] | None = None
    source: str | None = None
    mime_type: str | None = None


@dataclass(frozen=True)
class RetrievedCandidate:
    """Immutable carrier for one advanced-retrieval pipeline candidate.

    Score semantics (Part I): only ``final_score`` is consumed by downstream
    stages for ordering and top-n cuts. ``dense_score``, ``lexical_score``,
    ``rrf_score``, and ``rerank_score`` are diagnostic only.

    ``chunk.score`` from :class:`ScoredChunk` is ignored by advanced stages;
    use ``final_score`` instead.
    """

    chunk: ScoredChunk
    parent: str | None
    metadata: Mapping[str, object]
    final_score: float
    dense_score: float | None = None
    lexical_score: float | None = None
    rrf_score: float | None = None
    rerank_score: float | None = None

    def __post_init__(self) -> None:
        # Defensive copy + freeze so callers cannot mutate shared dict inputs.
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True)
class RetrievalRequest:
    """Input envelope for :class:`~app.ai.rag.pipeline.AdvancedRetrievalPipeline`."""

    question: str
    user_id: uuid.UUID
    top_k: int | None = None
    filters: MetadataFilter | None = None


@dataclass(frozen=True)
class RetrievalResult:
    """Output envelope from advanced retrieval (pre-prompt / pre-LLM)."""

    candidates: Sequence[RetrievedCandidate]
    citations: Sequence[Citation] = ()
    context_text: str = ""
    truncated: bool = False
    retrieval_latency_ms: int | None = None

    def __post_init__(self) -> None:
        # Accept list/iterable inputs at construction; store immutable tuples.
        object.__setattr__(self, "candidates", tuple(self.candidates))
        object.__setattr__(self, "citations", tuple(self.citations))


class IndexingJobState(str, Enum):
    """Lifecycle states for a document indexing job."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class IndexingJobStatus:
    """Status snapshot for an :class:`~app.ai.interfaces.indexing_job.IndexingJob`."""

    job_id: str
    state: IndexingJobState
    error_message: str | None = None
