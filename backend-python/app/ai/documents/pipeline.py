"""Parse and chunk orchestration (no embed or store in Phase 5)."""

from __future__ import annotations

from app.ai.documents.chunkers.recursive import RecursiveChunker
from app.ai.documents.parsers.router import select_parser
from app.ai.documents.schemas import DocumentChunk, ParsedDocument
from app.core.config import Settings


class IngestionPipeline:
    """Orchestrates parse → chunk for document ingestion."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._chunker = RecursiveChunker(settings)

    async def parse(
        self,
        file_bytes: bytes,
        filename: str,
        mime_type: str | None,
    ) -> ParsedDocument:
        parser = select_parser(mime_type, filename)
        return await parser.parse(file_bytes, filename)

    def chunk(self, document: ParsedDocument) -> list[DocumentChunk]:
        return self._chunker.chunk(document)
