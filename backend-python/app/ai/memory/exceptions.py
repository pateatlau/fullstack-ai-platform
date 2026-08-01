"""Memory subsystem exceptions (public API — stable after Phase 1)."""

from __future__ import annotations


class MemoryError(Exception):
    """Base exception for Memory subsystem errors."""

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class MemoryNotFoundError(MemoryError):
    """Raised when a requested memory record or preference does not exist."""

    def __init__(self, message: str = "Memory record not found.") -> None:
        super().__init__(message, code="memory_not_found")


class MemoryAccessDeniedError(MemoryError):
    """Raised when a caller attempts to access memory they do not own."""

    def __init__(self, message: str = "Access to this memory is denied.") -> None:
        super().__init__(message, code="memory_access_denied")
