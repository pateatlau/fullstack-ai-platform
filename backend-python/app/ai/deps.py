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
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.documents.pipeline import IngestionPipeline
from app.ai.embeddings.factory import create_embedding_provider
from app.ai.interfaces.embedding_provider import EmbeddingProvider
from app.ai.prompts.manager import PromptManager, create_prompt_manager
from app.ai.rag.context_builder import ContextBuilder
from app.ai.rag.prompt_builder import PromptBuilder
from app.ai.rag.retriever import Retriever
from app.ai.rag.service import RAGService
from app.providers.factory import ProviderFactory
from app.ai.tools.executor import ToolExecutor
from app.ai.tools.implementations.web_search import (
    WebSearchClient,
    create_tavily_client,
)
from app.ai.tools.registry import ToolRegistry
from app.ai.vectorstores.pgvector import PgVectorStore
from app.core.config import Settings, get_settings
from app.db.session import get_db_session
from app.services.document_service import DocumentService
from app.services.knowledge_service import KnowledgeService


def get_ai_settings(
    settings: Settings = Depends(get_settings),
) -> Settings:
    """Return validated application settings for AI-related wiring."""
    return settings


@lru_cache
def get_prompt_manager() -> PromptManager:
    """Return the process-wide ``PromptManager`` singleton (template cache warm)."""
    return create_prompt_manager()


@lru_cache
def get_tool_registry() -> ToolRegistry:
    """Return the process-wide ``ToolRegistry`` singleton."""
    return ToolRegistry()


@lru_cache
def get_web_search_client() -> WebSearchClient:
    """Return the process-wide Tavily-backed web search client."""
    return create_tavily_client(get_settings())


def get_tool_executor(
    registry: ToolRegistry = Depends(get_tool_registry),
    settings: Settings = Depends(get_settings),
) -> ToolExecutor:
    """Build a ``ToolExecutor`` wired to the app-scoped registry and settings."""
    return ToolExecutor(registry=registry, settings=settings)


@lru_cache
def get_embedding_provider() -> EmbeddingProvider:
    """Return the process-wide embedding provider (OpenAI in V1)."""
    return create_embedding_provider(get_settings())


def get_ingestion_pipeline(
    settings: Settings = Depends(get_ai_settings),
) -> IngestionPipeline:
    """Return a request-scoped ingestion pipeline (parse + chunk only)."""
    return IngestionPipeline(settings)


def get_ingestion_pipeline_with_embeddings(
    settings: Settings = Depends(get_ai_settings),
    embedding_provider: EmbeddingProvider = Depends(get_embedding_provider),
) -> IngestionPipeline:
    """Return a pipeline wired for in-memory parse → chunk → embed."""
    return IngestionPipeline(settings, embedding_provider=embedding_provider)


def get_document_service(
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_ai_settings),
    pipeline: IngestionPipeline = Depends(get_ingestion_pipeline),
) -> DocumentService:
    """Return a request-scoped ``DocumentService`` for auth-only ingestion."""
    return DocumentService(session=session, settings=settings, pipeline=pipeline)


def get_vector_store(
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_ai_settings),
) -> PgVectorStore:
    """Return a request-scoped pgvector store backed by the DB session."""
    return PgVectorStore(session=session, settings=settings)


def get_knowledge_service(
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_ai_settings),
    pipeline: IngestionPipeline = Depends(get_ingestion_pipeline_with_embeddings),
    vector_store: PgVectorStore = Depends(get_vector_store),
) -> KnowledgeService:
    """Return a request-scoped service for full vector ingest lifecycle."""
    return KnowledgeService(
        session=session,
        settings=settings,
        pipeline=pipeline,
        vector_store=vector_store,
    )


def get_retriever(
    embedding_provider: EmbeddingProvider = Depends(get_embedding_provider),
    vector_store: PgVectorStore = Depends(get_vector_store),
    settings: Settings = Depends(get_ai_settings),
) -> Retriever:
    """Return a request-scoped retriever wired to embed + vector search."""
    return Retriever(
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        settings=settings,
    )


def get_context_builder(
    settings: Settings = Depends(get_ai_settings),
) -> ContextBuilder:
    """Return a ``ContextBuilder`` using application RAG settings."""
    return ContextBuilder(settings)


def get_prompt_builder(
    prompt_manager: PromptManager = Depends(get_prompt_manager),
    settings: Settings = Depends(get_ai_settings),
) -> PromptBuilder:
    """Return a ``PromptBuilder`` wired to the app-scoped prompt manager."""
    return PromptBuilder(prompt_manager=prompt_manager, settings=settings)


def get_rag_service(
    retriever: Retriever = Depends(get_retriever),
    context_builder: ContextBuilder = Depends(get_context_builder),
    prompt_builder: PromptBuilder = Depends(get_prompt_builder),
    settings: Settings = Depends(get_ai_settings),
) -> RAGService:
    """Return a request-scoped ``RAGService`` wired to retrieval + LLM."""
    llm_provider = ProviderFactory.get_provider(settings=settings)
    return RAGService(
        retriever=retriever,
        context_builder=context_builder,
        prompt_builder=prompt_builder,
        llm_provider=llm_provider,
        settings=settings,
    )
