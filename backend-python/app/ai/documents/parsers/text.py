"""Plain-text and Markdown parser (UTF-8, no HTML rendering)."""

from __future__ import annotations

from app.ai.documents.schemas import ParsedDocument


class TextParser:
    async def parse(self, file_bytes: bytes, filename: str) -> ParsedDocument:
        text = file_bytes.decode("utf-8")
        return ParsedDocument(
            text=text,
            metadata={"source": filename},
        )
