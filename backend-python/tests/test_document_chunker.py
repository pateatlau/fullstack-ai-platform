"""Unit tests for RecursiveChunker."""

from __future__ import annotations

from app.ai.documents.chunkers.recursive import RecursiveChunker
from app.ai.documents.schemas import ParsedDocument
from app.core.config import Settings


def _settings(*, chunk_size: int = 100, chunk_overlap: int = 20) -> Settings:
    return Settings(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        openai_api_key="test-key",
    )


def test_recursive_chunker_respects_size_and_overlap() -> None:
    words = [f"word{i:04d}" for i in range(80)]
    text = " ".join(words)
    document = ParsedDocument(text=text, metadata={"source": "sample.txt"})
    chunks = RecursiveChunker(_settings(chunk_size=100, chunk_overlap=20)).chunk(
        document
    )

    assert len(chunks) >= 2
    for chunk in chunks:
        assert len(chunk.content) <= 100

    for index in range(len(chunks) - 1):
        left = chunks[index].content
        right = chunks[index + 1].content
        left_start = text.find(left)
        right_start = text.find(right)
        assert left_start >= 0
        assert right_start >= 0
        assert left_start + len(left) - right_start == 20


def test_recursive_chunker_is_deterministic() -> None:
    document = ParsedDocument(
        text="Paragraph one.\n\nParagraph two has more words.\n\nParagraph three.",
        metadata={"source": "sample.md"},
    )
    chunker = RecursiveChunker(_settings(chunk_size=40, chunk_overlap=10))
    first = chunker.chunk(document)
    second = chunker.chunk(document)
    assert [chunk.content for chunk in first] == [chunk.content for chunk in second]


def test_recursive_chunker_propagates_pdf_page_metadata() -> None:
    document = ParsedDocument(
        text="Page one text.\n\nPage two text.",
        metadata={
            "source": "sample.pdf",
            "pages": [
                {"page": 1, "text": "Page one text."},
                {"page": 2, "text": "Page two text."},
            ],
        },
    )
    chunks = RecursiveChunker(_settings(chunk_size=200, chunk_overlap=0)).chunk(
        document
    )
    assert chunks
    assert chunks[0].metadata["page"] == 1
