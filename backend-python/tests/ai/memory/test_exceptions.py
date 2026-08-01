"""Tests for Memory subsystem exceptions."""

from __future__ import annotations

from app.ai.memory.exceptions import (
    MemoryAccessDeniedError,
    MemoryError,
    MemoryNotFoundError,
)


class TestMemoryError:
    def test_base_error_carries_optional_code(self) -> None:
        error = MemoryError("Something went wrong.", code="memory_error")

        assert str(error) == "Something went wrong."
        assert error.code == "memory_error"

    def test_base_error_code_defaults_to_none(self) -> None:
        error = MemoryError("Something went wrong.")

        assert error.code is None


class TestMemoryNotFoundError:
    def test_default_message_and_code(self) -> None:
        error = MemoryNotFoundError()

        assert str(error) == "Memory record not found."
        assert error.code == "memory_not_found"

    def test_custom_message(self) -> None:
        error = MemoryNotFoundError("Preference key not found.")

        assert str(error) == "Preference key not found."
        assert error.code == "memory_not_found"

    def test_is_a_memory_error(self) -> None:
        assert isinstance(MemoryNotFoundError(), MemoryError)


class TestMemoryAccessDeniedError:
    def test_default_message_and_code(self) -> None:
        error = MemoryAccessDeniedError()

        assert str(error) == "Access to this memory is denied."
        assert error.code == "memory_access_denied"

    def test_custom_message(self) -> None:
        error = MemoryAccessDeniedError("You do not own this memory.")

        assert str(error) == "You do not own this memory."
        assert error.code == "memory_access_denied"

    def test_is_a_memory_error(self) -> None:
        assert isinstance(MemoryAccessDeniedError(), MemoryError)
