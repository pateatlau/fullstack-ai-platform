"""Package import tests — verify app.ai.memory has no circular imports."""

from __future__ import annotations

import importlib


def test_package_imports_cleanly() -> None:
    module = importlib.import_module("app.ai.memory")

    assert hasattr(module, "__all__")
    for name in module.__all__:
        assert hasattr(module, name), f"app.ai.memory is missing export {name!r}"


def test_public_api_surface_matches_phase_1_scope() -> None:
    module = importlib.import_module("app.ai.memory")

    assert set(module.__all__) == {
        "ConversationSummaryService",
        "LifecycleState",
        "MemoryAccessDeniedError",
        "MemoryContext",
        "MemoryError",
        "MemoryManager",
        "MemoryNotFoundError",
        "MemoryProvider",
        "MemoryRecord",
        "MemoryScope",
        "MemoryType",
        "PgVectorMemoryProvider",
    }


def test_subpackages_import_independently() -> None:
    importlib.import_module("app.ai.memory.models")
    importlib.import_module("app.ai.memory.lifecycle")
    importlib.import_module("app.ai.memory.exceptions")
    importlib.import_module("app.ai.memory.manager")
    importlib.import_module("app.ai.memory.interfaces")
    importlib.import_module("app.ai.memory.interfaces.memory_provider")
    importlib.import_module("app.ai.memory.providers")
    importlib.import_module("app.ai.memory.providers.pgvector")
    importlib.import_module("app.ai.memory.summarizer")
