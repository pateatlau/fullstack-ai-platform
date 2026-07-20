"""Character-based recursive chunker with overlap."""

from __future__ import annotations

import re

from app.ai.documents.schemas import DocumentChunk, ParsedDocument
from app.core.config import Settings

_SPLIT_PATTERN = re.compile(r"(\n\n+|\n|(?<=[.!?])\s+)")


class RecursiveChunker:
    """Split text on paragraph/sentence boundaries, then hard-split if needed."""

    def __init__(self, settings: Settings) -> None:
        self._chunk_size = settings.chunk_size
        self._chunk_overlap = settings.chunk_overlap

    def chunk(self, document: ParsedDocument) -> list[DocumentChunk]:
        source = str(document.metadata.get("source", ""))
        page_spans = _page_spans(document)
        text = document.text

        if not text:
            return []

        segments = _split_segments(text)
        chunks: list[DocumentChunk] = []
        start = 0
        chunk_index = 0

        while start < len(text):
            end = min(start + self._chunk_size, len(text))
            if end < len(text):
                end = _find_split_end(text, segments, start, end)

            content = text[start:end]
            if not content.strip():
                if end >= len(text):
                    break
                start = max(start + 1, end)
                continue

            metadata: dict[str, object] = {
                "source": source,
                "chunk_index": chunk_index,
                "page": _page_for_offset(page_spans, start),
                "tags": [],
            }
            chunks.append(
                DocumentChunk(
                    chunk_index=chunk_index,
                    content=content,
                    metadata=metadata,
                )
            )
            chunk_index += 1

            if end >= len(text):
                break

            next_start = end - self._chunk_overlap
            if next_start <= start:
                next_start = end
            start = next_start

        return chunks


def _split_segments(text: str) -> list[tuple[int, int]]:
    segments: list[tuple[int, int]] = []
    cursor = 0
    for match in _SPLIT_PATTERN.finditer(text):
        segments.append((cursor, match.start()))
        cursor = match.end()
    segments.append((cursor, len(text)))
    return segments


def _find_split_end(
    text: str,
    segments: list[tuple[int, int]],
    start: int,
    budget_end: int,
) -> int:
    best = budget_end
    for seg_start, seg_end in segments:
        if seg_end <= start:
            continue
        if seg_start >= budget_end:
            break
        if seg_start > start and seg_end <= budget_end:
            best = seg_end
    return best


def _page_spans(document: ParsedDocument) -> list[tuple[int, int, int]]:
    """Map character offsets to PDF page numbers when page metadata exists."""
    pages = document.metadata.get("pages")
    if not isinstance(pages, list):
        return []

    spans: list[tuple[int, int, int]] = []
    offset = 0
    for index, entry in enumerate(pages):
        if not isinstance(entry, dict):
            continue
        page_number = entry.get("page")
        page_text = entry.get("text")
        if not isinstance(page_number, int) or not isinstance(page_text, str):
            continue
        if not page_text:
            continue
        start = offset
        end = start + len(page_text)
        spans.append((start, end, page_number))
        offset = end
        if index + 1 < len(pages):
            offset += 2  # "\n\n" join between pages in ParsedDocument.text

    return spans


def _page_for_offset(page_spans: list[tuple[int, int, int]], offset: int) -> int | None:
    for start, end, page_number in page_spans:
        if start <= offset < end:
            return page_number
    return None
