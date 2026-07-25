"""Metadata filter helpers for advanced RAG retrieval (Phase 3).

Store push-down lives in :class:`~app.ai.vectorstores.pgvector.PgVectorStore`.
This module owns candidate-stage filtering (Part I stage 3) and shared
match / invalid-filter rules used by both paths.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.ai.rag.schemas import MetadataFilter, RetrievedCandidate


def is_unsatisfiable_filter(filters: MetadataFilter) -> bool:
    """Return True when the filter can never match (empty set predicates).

    Unsatisfiable filters yield an empty result — never an HTTP 500.
    """
    if filters.document_ids is not None and len(filters.document_ids) == 0:
        return True
    if filters.tags is not None and len(filters.tags) == 0:
        return True
    return False


def candidate_matches_filter(
    candidate: RetrievedCandidate,
    filters: MetadataFilter,
) -> bool:
    """Return whether a candidate satisfies all set filter predicates (AND)."""
    if is_unsatisfiable_filter(filters):
        return False

    if filters.document_ids is not None:
        if candidate.chunk.document_id not in filters.document_ids:
            return False

    if filters.tags is not None:
        raw_tags = candidate.metadata.get("tags")
        if not isinstance(raw_tags, list):
            return False
        chunk_tags = {tag for tag in raw_tags if isinstance(tag, str)}
        if not filters.tags.issubset(chunk_tags):
            return False

    if filters.source is not None:
        if candidate.metadata.get("source") != filters.source:
            return False

    if filters.mime_type is not None:
        if candidate.metadata.get("mime_type") != filters.mime_type:
            return False

    return True


def apply_metadata_filter(
    candidates: Sequence[RetrievedCandidate],
    filters: MetadataFilter | None,
) -> list[RetrievedCandidate]:
    """Filter candidates in place-order; ``None`` / no constraints pass through.

    Empty ``document_ids`` or ``tags`` frozensets yield ``[]``.
    """
    if filters is None:
        return list(candidates)
    if is_unsatisfiable_filter(filters):
        return []
    return [
        candidate
        for candidate in candidates
        if candidate_matches_filter(candidate, filters)
    ]
