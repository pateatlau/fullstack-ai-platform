"""In-memory document pipeline models (parse/chunk stages).

These are separate from SQLAlchemy ORM models in ``app/db/models.py``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ParsedDocument:
    """Structured text extracted from an uploaded file."""

    text: str
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class DocumentChunk:
    """A single text chunk with per-chunk metadata for downstream RAG.

    ``id`` is optional; when set (e.g. parent-child chunking), persistence
    writes that value as ``document_chunks.id`` so children can reference
    parents via ``metadata["parent_id"]`` before insert.
    """

    chunk_index: int
    content: str
    metadata: dict[str, object] = field(default_factory=dict)
    embedding: list[float] | None = None
    id: uuid.UUID | None = None
