"""In-memory document pipeline models (parse/chunk stages).

These are separate from SQLAlchemy ORM models in ``app/db/models.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ParsedDocument:
    """Structured text extracted from an uploaded file."""

    text: str
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class DocumentChunk:
    """A single text chunk with per-chunk metadata for downstream RAG."""

    chunk_index: int
    content: str
    metadata: dict[str, object] = field(default_factory=dict)
