"""Post-compression citation building for advanced RAG (Phase 8).

Citation indices ``[1..n]`` are assigned only after context compression,
in the same order as included context blocks, so prompt markers align with
API ``Citation.index`` values. ``score`` always reflects ``final_score``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from app.ai.interfaces.vector_store import ScoredChunk
from app.ai.rag.schemas import Citation, RetrievedCandidate
from app.core.config import Settings
from app.core.logging import get_logger

_logger = get_logger(__name__)


class CitationBuilder:
    """Build contiguous structured citations for included context blocks."""

    def __init__(self, *, settings: Settings) -> None:
        self._snippet_max_chars = settings.citation_snippet_max_chars

    def build(
        self,
        included_chunks: Sequence[ScoredChunk],
        *,
        candidates: Sequence[RetrievedCandidate] | None = None,
    ) -> list[Citation]:
        """Assign ``[1..n]`` for each included block (post-compression order).

        When ``candidates`` is provided, snippet text prefers the original
        candidate block (parent when set) and ``score`` uses ``final_score``.
        Otherwise falls back to the included chunk content / score.
        """
        if not included_chunks:
            return []

        by_chunk_id = _index_candidates(candidates)
        citations: list[Citation] = []

        for index, chunk in enumerate(included_chunks, start=1):
            candidate = by_chunk_id.get(chunk.chunk_id)
            metadata = (
                dict(candidate.metadata)
                if candidate is not None
                else dict(chunk.metadata)
            )
            source_text = (
                _block_text(candidate) if candidate is not None else chunk.content
            )
            score = candidate.final_score if candidate is not None else chunk.score
            citations.append(
                Citation(
                    index=index,
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    snippet=_snippet(source_text, self._snippet_max_chars),
                    score=score,
                    filename=_filename(metadata),
                    source=_optional_str(metadata.get("source")),
                    page=_optional_page(metadata.get("page")),
                )
            )

        _logger.info(
            "Citations built",
            citation_count=len(citations),
            snippet_max_chars=self._snippet_max_chars,
        )
        return citations


def _index_candidates(
    candidates: Sequence[RetrievedCandidate] | None,
) -> Mapping[object, RetrievedCandidate]:
    if not candidates:
        return {}
    # First occurrence wins; included blocks map 1:1 to a retrieved child.
    return {candidate.chunk.chunk_id: candidate for candidate in candidates}


def _block_text(candidate: RetrievedCandidate) -> str:
    """Prefer expanded parent text; fall back to child/flat chunk content."""
    if candidate.parent is not None and candidate.parent.strip():
        return candidate.parent
    return candidate.chunk.content


def _snippet(text: str, max_chars: int) -> str:
    """Bounded prefix of original source text (no paraphrase)."""
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


def _filename(metadata: Mapping[str, object]) -> str | None:
    explicit = _optional_str(metadata.get("filename"))
    if explicit is not None:
        return explicit
    return _optional_str(metadata.get("source"))


def _optional_str(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _optional_page(value: object) -> int | str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value:
        return value
    return None
