"""Unsupported document type error."""

from __future__ import annotations


class UnsupportedDocumentTypeError(ValueError):
    """Raised when MIME type or file extension is not in the V1 allowlist."""

    def __init__(self, *, filename: str, mime_type: str | None) -> None:
        mime_label = mime_type or "unknown"
        super().__init__(
            f"Unsupported document type for '{filename}' (mime={mime_label}). "
            "Supported types: PDF, DOCX, Markdown (.md), plain text (.txt)."
        )
        self.filename = filename
        self.mime_type = mime_type
