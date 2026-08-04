"""Package import tests — verify app.ai.memory has no circular imports."""

from __future__ import annotations

import importlib


def test_package_imports_cleanly() -> None:
    module = importlib.import_module("app.ai.memory")

    assert hasattr(module, "__all__")
    for name in module.__all__:
        assert hasattr(module, name), f"app.ai.memory is missing export {name!r}"


def test_public_api_surface_matches_locked_scope() -> None:
    """Part I § Public APIs: stable after Phase 1, plus ``MemoryPromptInjector``
    (documented Phase 8 addition — chat prompt-assembly integration).
    """
    module = importlib.import_module("app.ai.memory")

    assert set(module.__all__) == {
        "CandidateMemory",
        "ConversationSummaryService",
        "LifecycleManager",
        "LifecycleState",
        "MemoryAccessDeniedError",
        "MemoryContext",
        "MemoryContextBuilder",
        "MemoryError",
        "MemoryEvent",
        "MemoryEventType",
        "MemoryExtractor",
        "MemoryManager",
        "MemoryNotFoundError",
        "MemoryPolicyEngine",
        "MemoryPromptInjector",
        "MemoryProvider",
        "MemoryQualityEvaluator",
        "MemoryRecord",
        "MemoryScope",
        "MemoryType",
        "PgVectorMemoryProvider",
        "SemanticRetriever",
        "UserPreferenceItem",
        "UserPreferenceListResponse",
        "UserPreferenceUpsert",
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
    importlib.import_module("app.ai.memory.extraction")
    importlib.import_module("app.ai.memory.quality")
    importlib.import_module("app.ai.memory.summarizer")
    importlib.import_module("app.ai.memory.preferences")
    importlib.import_module("app.ai.memory.project")
    importlib.import_module("app.ai.memory.context_builder")
    importlib.import_module("app.ai.memory.semantic_retriever")
    importlib.import_module("app.ai.memory.token_budget")
    importlib.import_module("app.ai.memory.prompt_injector")
