"""Document parser protocol."""

from __future__ import annotations

from typing import Protocol

from app.ai.documents.schemas import ParsedDocument


class DocumentParser(Protocol):
    async def parse(self, file_bytes: bytes, filename: str) -> ParsedDocument: ...
