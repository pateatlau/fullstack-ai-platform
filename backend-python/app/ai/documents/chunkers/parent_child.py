"""Parent-child chunker for advanced RAG ingest.

Parents are large context windows; children are smaller retrieval units.
Each child metadata includes ``chunk_kind=child`` and ``parent_id`` (UUID of
the parent row). Parents use ``chunk_kind=parent`` and are not embedded by
default (see KnowledgeService).
"""

from __future__ import annotations

import uuid

from app.ai.documents.chunkers.recursive import RecursiveChunker
from app.ai.documents.schemas import DocumentChunk, ParsedDocument
from app.core.config import Settings


class ParentChildChunker:
    """Split text into parent windows, then child chunks linked by parent_id."""

    def __init__(self, settings: Settings) -> None:
        self._parent_chunker = RecursiveChunker(
            settings.model_copy(
                update={
                    "chunk_size": settings.parent_chunk_size,
                    "chunk_overlap": settings.parent_chunk_overlap,
                }
            )
        )
        self._child_chunker = RecursiveChunker(
            settings.model_copy(
                update={
                    "chunk_size": settings.child_chunk_size,
                    "chunk_overlap": settings.child_chunk_overlap,
                }
            )
        )

    def chunk(self, document: ParsedDocument) -> list[DocumentChunk]:
        source = str(document.metadata.get("source", ""))
        parents = self._parent_chunker.chunk(document)
        if not parents:
            return []

        result: list[DocumentChunk] = []
        chunk_index = 0
        for parent in parents:
            parent_id = uuid.uuid4()
            parent_metadata: dict[str, object] = {
                **parent.metadata,
                "source": source or str(parent.metadata.get("source", "")),
                "chunk_index": chunk_index,
                "chunk_kind": "parent",
            }
            result.append(
                DocumentChunk(
                    chunk_index=chunk_index,
                    content=parent.content,
                    metadata=parent_metadata,
                    id=parent_id,
                )
            )
            chunk_index += 1

            # Children are sliced from parent text; inherit page from parent
            # because offset-based PDF page spans do not apply to the slice.
            child_document = ParsedDocument(
                text=parent.content,
                metadata={"source": source},
            )
            children = self._child_chunker.chunk(child_document)
            parent_page = parent.metadata.get("page")
            for child in children:
                child_metadata: dict[str, object] = {
                    **child.metadata,
                    "source": source or str(child.metadata.get("source", "")),
                    "chunk_index": chunk_index,
                    "chunk_kind": "child",
                    "parent_id": str(parent_id),
                    "page": parent_page
                    if parent_page is not None
                    else child.metadata.get("page"),
                }
                result.append(
                    DocumentChunk(
                        chunk_index=chunk_index,
                        content=child.content,
                        metadata=child_metadata,
                        id=uuid.uuid4(),
                    )
                )
                chunk_index += 1

        return result
