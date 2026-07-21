"""Assemble retrieved chunks into a character-budgeted LLM context string."""

from __future__ import annotations

from dataclasses import dataclass

from app.ai.interfaces.vector_store import ScoredChunk
from app.core.config import Settings


@dataclass(frozen=True)
class BuiltContext:
    """Formatted context text plus metadata about budgeting decisions."""

    text: str
    included_chunks: list[ScoredChunk]
    truncated: bool


class ContextBuilder:
    """Format ranked chunks into numbered blocks with a character budget."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def build(
        self,
        chunks: list[ScoredChunk],
        *,
        max_chars: int | None = None,
    ) -> BuiltContext:
        if not chunks:
            return BuiltContext(text="", included_chunks=[], truncated=False)

        budget = (
            max_chars if max_chars is not None else self._settings.rag_context_max_chars
        )
        included = list(chunks)
        dropped = False

        while included:
            text = self._assemble(included)
            if len(text) <= budget:
                return BuiltContext(
                    text=text,
                    included_chunks=list(included),
                    truncated=dropped or len(included) < len(chunks),
                )
            included.pop()
            dropped = True

        return BuiltContext(text="", included_chunks=[], truncated=True)

    def _assemble(self, chunks: list[ScoredChunk]) -> str:
        blocks = [
            self._format_block(index, chunk)
            for index, chunk in enumerate(chunks, start=1)
        ]
        return "\n\n".join(blocks)

    def _format_block(self, index: int, chunk: ScoredChunk) -> str:
        header = f"[{index}]"
        source = chunk.metadata.get("source")
        if isinstance(source, str) and source:
            header = f"[{index}] (source: {source})"
        return f"{header}\n{chunk.content}"
