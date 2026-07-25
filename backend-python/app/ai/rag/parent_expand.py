"""Parent expansion helper for advanced retrieval (Phase 2).

Attaches parent text to child candidates and collapses multiple children that
share one parent into a single candidate (one parent context block). Orphan
children (missing/invalid parent) keep ``parent=None`` so downstream stages
use child content as the block.

Wired into :class:`DefaultAdvancedRetrievalPipeline` when a parent content
fetcher is provided (Phase 10 DI).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence

from app.ai.rag.schemas import RetrievedCandidate

ParentContentFetcher = Callable[
    [Sequence[uuid.UUID]],
    Awaitable[Mapping[uuid.UUID, str]],
]


def _parse_parent_id(metadata: Mapping[str, object]) -> uuid.UUID | None:
    raw = metadata.get("parent_id")
    if raw is None:
        return None
    if isinstance(raw, uuid.UUID):
        return raw
    if isinstance(raw, str):
        try:
            return uuid.UUID(raw)
        except ValueError:
            return None
    return None


async def expand_parents(
    candidates: Sequence[RetrievedCandidate],
    *,
    fetch_parent_contents: ParentContentFetcher,
) -> list[RetrievedCandidate]:
    """Expand and dedupe candidates by parent_id.

    Input order is treated as rank order (``final_score`` already applied by
    the caller). The first candidate for each parent_id is kept; later siblings
    are dropped. Missing parents leave ``parent=None`` (orphan → child block).
    """
    if not candidates:
        return []

    parent_ids: list[uuid.UUID] = []
    seen_fetch: set[uuid.UUID] = set()
    for candidate in candidates:
        parent_id = _parse_parent_id(candidate.metadata)
        if parent_id is not None and parent_id not in seen_fetch:
            seen_fetch.add(parent_id)
            parent_ids.append(parent_id)

    contents = await fetch_parent_contents(parent_ids) if parent_ids else {}

    expanded: list[RetrievedCandidate] = []
    seen_parents: set[uuid.UUID] = set()
    for candidate in candidates:
        parent_id = _parse_parent_id(candidate.metadata)
        if parent_id is None:
            expanded.append(
                RetrievedCandidate(
                    chunk=candidate.chunk,
                    parent=None,
                    metadata=dict(candidate.metadata),
                    final_score=candidate.final_score,
                    dense_score=candidate.dense_score,
                    lexical_score=candidate.lexical_score,
                    rrf_score=candidate.rrf_score,
                    rerank_score=candidate.rerank_score,
                )
            )
            continue

        if parent_id in seen_parents:
            continue
        seen_parents.add(parent_id)

        parent_text = contents.get(parent_id)
        expanded.append(
            RetrievedCandidate(
                chunk=candidate.chunk,
                parent=parent_text,
                metadata=dict(candidate.metadata),
                final_score=candidate.final_score,
                dense_score=candidate.dense_score,
                lexical_score=candidate.lexical_score,
                rrf_score=candidate.rrf_score,
                rerank_score=candidate.rerank_score,
            )
        )

    return expanded
