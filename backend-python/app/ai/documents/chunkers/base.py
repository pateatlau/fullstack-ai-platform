"""Chunker protocol."""

from __future__ import annotations

from typing import Protocol

from app.ai.documents.schemas import DocumentChunk, ParsedDocument


class Chunker(Protocol):
    def chunk(self, document: ParsedDocument) -> list[DocumentChunk]: ...
