"""Unit tests for document parsers and router."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.ai.documents.parsers.docx import DocxParser
from app.ai.documents.parsers.errors import UnsupportedDocumentTypeError
from app.ai.documents.parsers.pdf import PdfParser
from app.ai.documents.parsers.router import select_parser
from app.ai.documents.parsers.text import TextParser

FIXTURES = Path(__file__).resolve().parent / "data" / "documents"


@pytest.mark.anyio
async def test_pdf_parser_extracts_text_and_page_metadata() -> None:
    file_bytes = (FIXTURES / "sample.pdf").read_bytes()
    parsed = await PdfParser().parse(file_bytes, "sample.pdf")

    assert "Page one content" in parsed.text
    assert "Page two content" in parsed.text
    assert parsed.metadata["page_count"] == 2
    pages = parsed.metadata["pages"]
    assert isinstance(pages, list)
    assert len(pages) == 2
    assert pages[0]["page"] == 1
    assert pages[1]["page"] == 2


@pytest.mark.anyio
async def test_docx_parser_extracts_paragraphs() -> None:
    file_bytes = (FIXTURES / "sample.docx").read_bytes()
    parsed = await DocxParser().parse(file_bytes, "sample.docx")

    assert "First paragraph in DOCX fixture." in parsed.text
    assert "Second paragraph in DOCX fixture." in parsed.text


@pytest.mark.anyio
async def test_text_parser_reads_markdown_fixture() -> None:
    file_bytes = (FIXTURES / "sample.md").read_bytes()
    parsed = await TextParser().parse(file_bytes, "sample.md")

    assert "# Sample Markdown" in parsed.text
    assert "Markdown fixture paragraph." in parsed.text


@pytest.mark.anyio
async def test_text_parser_reads_plain_text_fixture() -> None:
    file_bytes = (FIXTURES / "sample.txt").read_bytes()
    parsed = await TextParser().parse(file_bytes, "sample.txt")

    assert parsed.text == "Plain text fixture content.\n"


def test_select_parser_routes_by_extension_and_mime() -> None:
    assert isinstance(select_parser("application/pdf", "report.pdf"), PdfParser)
    assert isinstance(
        select_parser(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "notes.docx",
        ),
        DocxParser,
    )
    assert isinstance(select_parser("text/markdown", "readme.md"), TextParser)
    assert isinstance(select_parser("text/plain", "notes.txt"), TextParser)


def test_select_parser_rejects_unsupported_types() -> None:
    with pytest.raises(UnsupportedDocumentTypeError):
        select_parser("text/html", "page.html")

    with pytest.raises(UnsupportedDocumentTypeError):
        select_parser(None, "data.csv")
