"""PyMuPDF-backed PDF parser."""

from __future__ import annotations

import asyncio

import fitz

from app.ai.documents.schemas import ParsedDocument


class PdfParser:
    async def parse(self, file_bytes: bytes, filename: str) -> ParsedDocument:
        return await asyncio.to_thread(self._parse_sync, file_bytes, filename)

    def _parse_sync(self, file_bytes: bytes, filename: str) -> ParsedDocument:
        pages: list[dict[str, object]] = []
        text_parts: list[str] = []

        with fitz.open(stream=file_bytes, filetype="pdf") as pdf_document:
            for page_index in range(pdf_document.page_count):
                page_number = page_index + 1
                page = pdf_document[page_index]
                page_text = str(page.get_text()).strip()
                pages.append({"page": page_number, "text": page_text})
                if page_text:
                    text_parts.append(page_text)

        return ParsedDocument(
            text="\n\n".join(text_parts),
            metadata={
                "source": filename,
                "page_count": len(pages),
                "pages": pages,
            },
        )
