"""FastAPI dependency providers for AI framework components.

Phase 1 establishes the DI wiring pattern only. Concrete providers for
prompts, tools, documents, embeddings, vector stores, and RAG orchestration
are registered in later phases via ``Depends(...)`` helpers here.

App-scoped dependencies (for example a ``PromptManager`` singleton) and
request-scoped dependencies follow the same pattern as ``app/db/deps.py``.
"""

from __future__ import annotations

from functools import lru_cache

from fastapi import Depends

from app.ai.prompts.manager import PromptManager, create_prompt_manager
from app.core.config import Settings, get_settings


def get_ai_settings(
    settings: Settings = Depends(get_settings),
) -> Settings:
    """Return validated application settings for AI-related wiring."""
    return settings


@lru_cache
def get_prompt_manager() -> PromptManager:
    """Return the process-wide ``PromptManager`` singleton (template cache warm)."""
    return create_prompt_manager()


# Phase 3+: get_tool_registry() -> ToolRegistry (app-scoped singleton)
# Phase 4+: get_web_search_client() -> WebSearchClient (app-scoped)
# Phase 5+: get_document_ingestion_service() -> DocumentIngestionService
# Phase 8+: get_rag_service() -> RAGService (app-scoped singleton)
