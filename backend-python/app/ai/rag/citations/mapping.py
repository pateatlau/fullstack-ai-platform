"""Map internal Citation models to additive HTTP CitationSchema DTOs."""

from __future__ import annotations

from collections.abc import Sequence

from app.ai.rag.schemas import Citation
from app.schemas.chat import CitationSchema


def to_citation_schema(citation: Citation) -> CitationSchema:
    """Convert one domain citation to the public API schema."""
    return CitationSchema(
        index=citation.index,
        chunk_id=citation.chunk_id,
        document_id=citation.document_id,
        snippet=citation.snippet,
        score=citation.score,
        filename=citation.filename,
        source=citation.source,
        page=citation.page,
    )


def to_citation_schemas(
    citations: Sequence[Citation] | None,
) -> list[CitationSchema] | None:
    """Pass through ``None``; otherwise map each citation (may be empty)."""
    if citations is None:
        return None
    return [to_citation_schema(citation) for citation in citations]
