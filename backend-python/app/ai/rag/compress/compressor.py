"""Faithful context compression for advanced RAG (Phase 7).

Select / prefix-trim / remove candidates by ``final_score`` to fit
``max_chars``. Never rewrite, summarize, or paraphrase source text —
trimmed content is always a prefix of the original block text.

Block text prefers expanded ``parent`` when set (non-empty), else
``chunk.content`` — same rule as the Cohere rerank adapter.
"""

from __future__ import annotations

from dataclasses import replace

from app.ai.interfaces.vector_store import ScoredChunk
from app.ai.rag.context_builder import BuiltContext, ContextBuilder
from app.ai.rag.schemas import RetrievedCandidate
from app.core.config import Settings
from app.core.logging import get_logger

_logger = get_logger(__name__)


class FaithfulContextCompressor:
    """Pack candidates into a character budget using original source text only.

    When packing cannot include any block, falls back to V1
    :class:`~app.ai.rag.context_builder.ContextBuilder` tail-drop behaviour
    (still no paraphrase). Logs counts/budget only — never raw chunk text.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        context_builder: ContextBuilder | None = None,
    ) -> None:
        self._settings = settings
        self._context_builder = context_builder or ContextBuilder(settings)

    def compress(
        self,
        candidates: list[RetrievedCandidate],
        *,
        max_chars: int,
    ) -> BuiltContext:
        if not candidates:
            return BuiltContext(text="", included_chunks=[], truncated=False)

        # Downstream ordering uses final_score only (Part I score semantics).
        ordered = sorted(candidates, key=lambda c: c.final_score, reverse=True)
        scored = [_to_scored_chunk(candidate) for candidate in ordered]
        packed = _pack(scored, max_chars=max_chars)

        if packed.included_chunks:
            _logger.info(
                "Context compression complete",
                candidate_count=len(candidates),
                included_count=len(packed.included_chunks),
                max_chars=max_chars,
                compression_truncated=packed.truncated,
            )
            return packed

        # Empty pack with non-empty input → V1-style tail-drop fallback.
        _logger.info(
            "Context compression fallback to ContextBuilder",
            candidate_count=len(candidates),
            max_chars=max_chars,
            compression_fallback=True,
        )
        return self._context_builder.build(scored, max_chars=max_chars)


def _to_scored_chunk(candidate: RetrievedCandidate) -> ScoredChunk:
    """Map a candidate to a ScoredChunk using the context block text."""
    content = _block_text(candidate)
    chunk = candidate.chunk
    return ScoredChunk(
        chunk_id=chunk.chunk_id,
        document_id=chunk.document_id,
        chunk_index=chunk.chunk_index,
        content=content,
        metadata=dict(chunk.metadata),
        score=candidate.final_score,
    )


def _block_text(candidate: RetrievedCandidate) -> str:
    """Prefer expanded parent text; fall back to child/flat chunk content."""
    if candidate.parent is not None and candidate.parent.strip():
        return candidate.parent
    return candidate.chunk.content


def _pack(chunks: list[ScoredChunk], *, max_chars: int) -> BuiltContext:
    """Greedy select by list order; prefix-trim the first block that won't fit."""
    included: list[ScoredChunk] = []
    truncated = False

    for chunk in chunks:
        trial = [*included, chunk]
        if len(_assemble(trial)) <= max_chars:
            included = trial
            continue

        trimmed = _prefix_trim(chunk, included=included, max_chars=max_chars)
        if trimmed is None:
            # Remaining budget cannot hold another block header + content.
            truncated = True
            break

        included.append(trimmed)
        truncated = True
        # Remaining budget is consumed (or exhausted) by the trimmed block.
        break

    if len(included) < len(chunks):
        truncated = True

    if not included:
        return BuiltContext(text="", included_chunks=[], truncated=bool(chunks))

    return BuiltContext(
        text=_assemble(included),
        included_chunks=list(included),
        truncated=truncated,
    )


def _prefix_trim(
    chunk: ScoredChunk,
    *,
    included: list[ScoredChunk],
    max_chars: int,
) -> ScoredChunk | None:
    """Return a prefix-trimmed copy of ``chunk`` that fits, or ``None``."""
    index = len(included) + 1
    header = _format_header(index, chunk)
    base = f"{header}\n"
    if included:
        overhead = len(_assemble(included)) + len("\n\n") + len(base)
    else:
        overhead = len(base)

    available = max_chars - overhead
    if available <= 0:
        return None

    trimmed_content = chunk.content[:available]
    if not trimmed_content:
        return None

    return replace(chunk, content=trimmed_content)


def _assemble(chunks: list[ScoredChunk]) -> str:
    blocks = [
        _format_block(index, chunk) for index, chunk in enumerate(chunks, start=1)
    ]
    return "\n\n".join(blocks)


def _format_block(index: int, chunk: ScoredChunk) -> str:
    return f"{_format_header(index, chunk)}\n{chunk.content}"


def _format_header(index: int, chunk: ScoredChunk) -> str:
    # Match V1 ContextBuilder block headers for BuiltContext compatibility.
    source = chunk.metadata.get("source")
    if isinstance(source, str) and source:
        return f"[{index}] (source: {source})"
    return f"[{index}]"
