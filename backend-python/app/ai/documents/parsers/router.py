"""Simple if/else parser routing by MIME type and file extension."""

from __future__ import annotations

from pathlib import PurePath

from app.ai.documents.parsers.base import DocumentParser
from app.ai.documents.parsers.docx import DocxParser
from app.ai.documents.parsers.errors import UnsupportedDocumentTypeError
from app.ai.documents.parsers.pdf import PdfParser
from app.ai.documents.parsers.text import TextParser

_PDF_MIMES = frozenset({"application/pdf"})
_DOCX_MIMES = frozenset(
    {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
)
_TEXT_MIMES = frozenset({"text/plain", "text/markdown", "text/x-markdown"})

_pdf_parser = PdfParser()
_docx_parser = DocxParser()
_text_parser = TextParser()


def select_parser(mime_type: str | None, filename: str) -> DocumentParser:
    """Return the parser for an allowed MIME/extension pair."""
    extension = PurePath(filename).suffix.lower()

    if extension == ".pdf" or (mime_type and mime_type in _PDF_MIMES):
        return _pdf_parser
    if extension == ".docx" or (mime_type and mime_type in _DOCX_MIMES):
        return _docx_parser
    if extension == ".md":
        return _text_parser
    if extension == ".txt" or (mime_type and mime_type in _TEXT_MIMES):
        return _text_parser

    raise UnsupportedDocumentTypeError(filename=filename, mime_type=mime_type)


def is_supported_document_type(mime_type: str | None, filename: str) -> bool:
    """Return whether the file type is in the V1 allowlist."""
    try:
        select_parser(mime_type, filename)
    except UnsupportedDocumentTypeError:
        return False
    return True
