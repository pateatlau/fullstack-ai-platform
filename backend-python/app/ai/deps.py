"""FastAPI dependency providers for AI framework components.

Phase 1 establishes the DI wiring pattern only. Concrete providers for
prompts, tools, documents, embeddings, vector stores, and RAG orchestration
are registered in later phases via ``Depends(...)`` helpers here.

App-scoped dependencies (for example a ``PromptManager`` singleton) and
request-scoped dependencies follow the same pattern as ``app/db/deps.py``.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING, Callable

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from app.ai.agent.executor.agent_executor import AgentExecutor
    from app.ai.agent.scratchpad.store import ScratchpadStore
    from app.ai.hitl.notifications import NotificationDispatcher
    from app.ai.hitl.policy import ApprovalPolicy
    from app.ai.hitl.rules import RulePolicyEngine
    from app.ai.hitl.store import ApprovalsStore
    from app.ai.hitl.service import AgentApprovalService
    from app.ai.mcp.registry import McpServerRegistry
    from app.ai.security.rbac.service import RbacService
    from app.ai.memory.context_builder import MemoryContextBuilder
    from app.ai.memory.manager import MemoryManager
    from app.ai.memory.providers.pgvector import PgVectorMemoryProvider
    from app.ai.memory.semantic_retriever import SemanticRetriever
    from app.ai.memory.summarizer import ConversationSummaryService
    from app.ai.voice.config import VoiceConfig
    from app.ai.voice.interfaces import SttProvider, TtsProvider
    from app.ai.voice.interrupt import InterruptController
    from app.ai.voice.providers.openai_voice import OpenAiVoiceAdapter
    from app.ai.voice.session import VoiceSessionManager
    from app.ai.workflow.manager import WorkflowManager
    from app.ai.workflow.providers.postgres import PostgresWorkflowStore
    from app.ai.observability.aggregation.usage_aggregator import UsageAggregator
    from app.ai.jobs.queue import PostgresJobQueue
    from app.ai.jobs.schedule_store import PostgresJobScheduleStore

from app.ai.agent.runtime.default_agent import DefaultAgent
from app.ai.agent.runtime.factory import create_default_agent
from app.ai.documents.pipeline import IngestionPipeline
from app.ai.embeddings.factory import create_embedding_provider
from app.ai.interfaces.embedding_provider import EmbeddingProvider
from app.ai.prompts.manager import PromptManager
from app.ai.prompts.repository import PromptRepository
from app.ai.prompts.renderer import PromptRenderer
from app.ai.rag.citations import CitationBuilder
from app.ai.rag.compress import FaithfulContextCompressor
from app.ai.rag.context_builder import ContextBuilder
from app.ai.rag.hybrid import HybridRetriever
from app.ai.rag.pipeline import (
    AdvancedRetrievalPipeline,
    DefaultAdvancedRetrievalPipeline,
)
from app.ai.rag.prompt_builder import PromptBuilder
from app.ai.rag.rerank import CohereReranker
from app.ai.rag.retriever import Retriever
from app.ai.rag.rewrite import LLMQueryRewriter
from app.ai.rag.service import RAGService
from app.ai.plugins.registry import PluginRegistry
from app.ai.plugins.store import PluginsStore
from app.ai.plugins.workflow.registry import WorkflowPluginRegistry
from app.ai.tools.executor import ToolExecutor
from app.ai.tools.implementations.web_search import (
    WebSearchClient,
    create_tavily_client,
)
from app.ai.tools.registry import ToolRegistry
from app.ai.vectorstores.pgvector import PgVectorStore
from app.core.config import Settings, get_settings
from app.db.documents import SqlDocumentStore
from app.db.identity import SqlUploadQuotaStore
from app.db.session import get_db_session
from app.providers.factory import ProviderFactory
from app.services.document_service import DocumentService


def get_ai_settings(
    settings: Settings = Depends(get_settings),
) -> Settings:
    """Return validated application settings for AI-related wiring."""
    return settings


def get_rbac_service(
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_ai_settings),
) -> "RbacService":
    """Return a request-scoped ``RbacService`` (Epic 11 Phase 2).

    Construction is cheap and side-effect free; callers gate any actual
    permission check on ``security_governance_enabled`` /
    ``security_rbac_enforcement_enabled`` so behaviour is unchanged when the
    flags are off.
    """
    from app.ai.security.rbac.service import RbacService
    from app.ai.security.rbac.store import PostgresRoleStore

    return RbacService(
        PostgresRoleStore(session),
        cache_ttl_seconds=settings.security_rbac_cache_ttl_seconds,
    )


@lru_cache
def get_prompt_repository() -> PromptRepository:
    """Return the process-wide ``PromptRepository`` singleton."""
    return PromptRepository()


@lru_cache
def get_prompt_manager() -> PromptManager:
    """Return the process-wide ``PromptManager`` singleton (template cache warm)."""
    return PromptManager(
        repository=get_prompt_repository(),
        renderer=PromptRenderer(),
    )


@lru_cache
def get_tool_registry() -> ToolRegistry:
    """Return the process-wide ``ToolRegistry`` singleton."""
    return ToolRegistry()


@lru_cache
def get_plugin_registry() -> PluginRegistry:
    """Return the process-wide ``PluginRegistry`` singleton."""
    return PluginRegistry()


@lru_cache
def get_plugins_store() -> PluginsStore:
    """Return the process-wide ``PluginsStore`` singleton."""
    return PluginsStore(get_plugin_registry())


@lru_cache
def get_workflow_plugin_registry() -> WorkflowPluginRegistry:
    """Return the process-wide ``WorkflowPluginRegistry`` singleton."""
    return WorkflowPluginRegistry()


@lru_cache
def get_web_search_client() -> WebSearchClient:
    """Return the process-wide Tavily-backed web search client."""
    return create_tavily_client(get_settings())


def _create_tool_executor(
    *,
    registry: ToolRegistry,
    settings: Settings,
    rbac_service: "RbacService | None" = None,
) -> ToolExecutor:
    """Build a ``ToolExecutor`` with MCP permission policy when MCP is enabled."""
    mcp_permission_policy = None
    if settings.mcp_enabled:
        from app.ai.mcp.permissions import McpPermissionPolicy

        mcp_permission_policy = McpPermissionPolicy(
            config=settings.mcp_permission_policy
        )

    return ToolExecutor(
        registry=registry,
        settings=settings,
        mcp_permission_policy=mcp_permission_policy,
        rbac_service=rbac_service,
    )


def get_tool_executor(
    registry: ToolRegistry = Depends(get_tool_registry),
    settings: Settings = Depends(get_settings),
    rbac_service: "RbacService | None" = Depends(get_rbac_service),
) -> ToolExecutor:
    """Build a ``ToolExecutor`` wired to the app-scoped registry and settings.

    Phase 9: Includes MCP permission policy when MCP is enabled.
    Epic 11 Phase 2: Includes RBAC service for tool-tier authorization
    (only consulted when Security & Governance RBAC enforcement is on).
    """
    return _create_tool_executor(
        registry=registry, settings=settings, rbac_service=rbac_service
    )


def get_hitl_rule_engine(
    settings: Settings = Depends(get_ai_settings),
) -> "RulePolicyEngine | None":
    """Return the rule-based approval policy engine (recommendation #1).

    ``None`` when unconfigured, so ``ApprovalPolicy`` falls back to the
    legacy ``requires_approval``/``hitl_required_tool_names`` gate exactly as
    before rule support existed.
    """
    if not settings.hitl_policy_rules:
        return None
    from app.ai.hitl.rules import RulePolicyEngine, load_rules_from_config

    return RulePolicyEngine(load_rules_from_config(settings.hitl_policy_rules))


def get_approval_policy(
    settings: Settings = Depends(get_ai_settings),
    rule_engine: "RulePolicyEngine | None" = Depends(get_hitl_rule_engine),
) -> "ApprovalPolicy":
    """Return the process-wide approval policy from settings."""
    from app.ai.hitl.policy import ApprovalPolicy

    return ApprovalPolicy(
        required_tool_names=frozenset(settings.hitl_required_tool_names),
        rule_engine=rule_engine,
        environment=settings.app_env,
    )


@lru_cache
def get_hitl_notification_dispatcher() -> "NotificationDispatcher | None":
    """Return the process-wide outbound approval notification dispatcher.

    ``None`` when no providers are configured, matching ``ApprovalPolicy``'s
    "absent means legacy/no-op behavior" convention.
    """
    settings = get_settings()
    if not settings.hitl_notification_providers:
        return None
    from app.ai.hitl.notifications import (
        DiscordNotificationProvider,
        InAppNotificationProvider,
        NotificationDispatcher,
        NotificationProvider,
        SlackNotificationProvider,
        TeamsNotificationProvider,
        WebhookNotificationProvider,
    )

    def _webhook_provider(
        factory: Callable[..., NotificationProvider], webhook_url: str | None
    ) -> NotificationProvider:
        # ``Settings.validate_hitl_requirements`` already fails startup if a
        # listed provider is missing its URL, so this is just narrowing the
        # optional type for the constructor below.
        assert webhook_url is not None
        return factory(
            webhook_url=webhook_url,
            timeout_seconds=settings.hitl_notification_timeout_seconds,
        )

    provider_factories: dict[str, Callable[[], NotificationProvider]] = {
        "webhook": lambda: _webhook_provider(
            WebhookNotificationProvider, settings.hitl_notification_webhook_url
        ),
        "slack": lambda: _webhook_provider(
            SlackNotificationProvider, settings.hitl_notification_slack_webhook_url
        ),
        "teams": lambda: _webhook_provider(
            TeamsNotificationProvider, settings.hitl_notification_teams_webhook_url
        ),
        "discord": lambda: _webhook_provider(
            DiscordNotificationProvider, settings.hitl_notification_discord_webhook_url
        ),
        "in_app": InAppNotificationProvider,
    }
    providers = [
        provider_factories[name]()
        for name in settings.hitl_notification_providers
        if name in provider_factories
    ]
    return NotificationDispatcher(providers)


def get_agent_approval_service(
    session: AsyncSession = Depends(get_db_session),
    tool_registry: ToolRegistry = Depends(get_tool_registry),
    tool_executor: ToolExecutor = Depends(get_tool_executor),
    settings: Settings = Depends(get_ai_settings),
    rbac_service: "RbacService" = Depends(get_rbac_service),
) -> "AgentApprovalService":
    """Return a request-scoped agent approval orchestrator."""
    from app.ai.hitl.service import AgentApprovalService
    from app.ai.hitl.store import AgentToolApprovalStore
    from app.db.chat import SqlChatStore

    return AgentApprovalService(
        approval_store=AgentToolApprovalStore(
            session,
            client_audit_retention_days=settings.hitl_client_audit_retention_days,
        ),
        chat_store=SqlChatStore(session),
        tool_registry=tool_registry,
        tool_executor=tool_executor,
        approval_timeout_hours=settings.hitl_approval_timeout_hours,
        default_model=settings.default_llm_model(),
        notification_dispatcher=get_hitl_notification_dispatcher(),
        rbac_service=rbac_service,
        rbac_enforcement_enabled=(
            settings.security_governance_enabled
            and settings.security_rbac_enforcement_enabled
        ),
    )


def get_approvals_store(
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_ai_settings),
) -> "ApprovalsStore":
    """Return a request-scoped unified approvals read store."""
    from app.ai.hitl.store import ApprovalsStore

    return ApprovalsStore(
        session,
        client_audit_retention_days=settings.hitl_client_audit_retention_days,
    )


def get_agent_runtime(
    settings: Settings = Depends(get_ai_settings),
    tool_registry: ToolRegistry = Depends(get_tool_registry),
    prompt_manager: PromptManager = Depends(get_prompt_manager),
    tool_executor: ToolExecutor = Depends(get_tool_executor),
    approval_policy: "ApprovalPolicy" = Depends(get_approval_policy),
    approval_service: "AgentApprovalService" = Depends(get_agent_approval_service),
) -> DefaultAgent:
    """Return a request-scoped :class:`DefaultAgent` wired to AI dependencies."""
    return create_default_agent(
        settings=settings,
        tool_registry=tool_registry,
        prompt_manager=prompt_manager,
        tool_executor=tool_executor,
        approval_policy=approval_policy if settings.hitl_enabled else None,
        approval_service=approval_service if settings.hitl_enabled else None,
    )


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


def get_upload_quota_service(
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_ai_settings),
):
    """Upload-only quota wiring (guest message counters unused on document routes)."""
    from app.services.quota_service import QuotaService

    return QuotaService(
        store=_NoopGuestQuotaStore(),
        upload_store=SqlUploadQuotaStore(session),
        settings=settings,
    )


class _NoopGuestQuotaStore:
    async def get_message_count(self, guest_id: object, window_start: object) -> int:
        del guest_id, window_start
        return 0

    async def increment(
        self,
        guest_id: object,
        window_start: object,
        *,
        tokens: int = 0,
    ) -> None:
        del guest_id, window_start, tokens


def get_knowledge_service(
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_ai_settings),
    pipeline: IngestionPipeline = Depends(get_ingestion_pipeline_with_embeddings),
    vector_store: PgVectorStore = Depends(get_vector_store),
    quota_service=Depends(get_upload_quota_service),
):
    """Return a request-scoped service for full vector ingest lifecycle."""
    from app.ai.jobs.queue import PostgresJobQueue
    from app.db.engine import get_sessionmaker
    from app.services.knowledge_service import KnowledgeService

    job_queue = None
    if settings.background_jobs_enabled:
        job_queue = PostgresJobQueue(get_sessionmaker(), settings)

    return KnowledgeService(
        session=session,
        settings=settings,
        pipeline=pipeline,
        vector_store=vector_store,
        quota_service=quota_service,
        job_queue=job_queue,
    )


def get_job_queue(
    settings: Settings = Depends(get_ai_settings),
) -> "PostgresJobQueue":
    """Return a request-scoped Postgres job queue."""
    from app.ai.jobs.queue import PostgresJobQueue
    from app.db.engine import get_sessionmaker

    return PostgresJobQueue(get_sessionmaker(), settings)


def get_job_schedule_store() -> "PostgresJobScheduleStore":
    """Return a request-scoped schedule store."""
    from app.ai.jobs.schedule_store import PostgresJobScheduleStore
    from app.db.engine import get_sessionmaker

    return PostgresJobScheduleStore(get_sessionmaker())


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


def get_hybrid_retriever(
    embedding_provider: EmbeddingProvider = Depends(get_embedding_provider),
    vector_store: PgVectorStore = Depends(get_vector_store),
    settings: Settings = Depends(get_ai_settings),
) -> HybridRetriever:
    """Return a request-scoped hybrid dense + FTS retriever (RRF fusion)."""
    return HybridRetriever(
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


def get_advanced_retrieval_pipeline(
    hybrid_retriever: HybridRetriever = Depends(get_hybrid_retriever),
    context_builder: ContextBuilder = Depends(get_context_builder),
    prompt_manager: PromptManager = Depends(get_prompt_manager),
    settings: Settings = Depends(get_ai_settings),
    session: AsyncSession = Depends(get_db_session),
) -> AdvancedRetrievalPipeline:
    """Return the flag-on advanced retrieval orchestrator (Phase 10).

    Always constructed for DI; ``UnifiedChatService`` / ``RAGService`` only
    call it when ``advanced_rag_enabled`` is true. Missing Cohere key falls
    back to pre-rerank order inside the adapter.
    """
    document_store = SqlDocumentStore(session)
    rewriter = LLMQueryRewriter(
        provider=ProviderFactory.get_provider(settings=settings),
        prompt_manager=prompt_manager,
        settings=settings,
    )
    return DefaultAdvancedRetrievalPipeline(
        hybrid_retriever=hybrid_retriever,
        parent_content_fetcher=document_store.get_chunk_contents_by_ids,
        query_rewriter=rewriter,
        reranker=CohereReranker(settings=settings),
        context_compressor=FaithfulContextCompressor(
            settings=settings,
            context_builder=context_builder,
        ),
        citation_builder=CitationBuilder(settings=settings),
        settings=settings,
    )


def get_rag_service(
    retriever: Retriever = Depends(get_retriever),
    context_builder: ContextBuilder = Depends(get_context_builder),
    prompt_builder: PromptBuilder = Depends(get_prompt_builder),
    settings: Settings = Depends(get_ai_settings),
    advanced_pipeline: AdvancedRetrievalPipeline = Depends(
        get_advanced_retrieval_pipeline
    ),
) -> RAGService:
    """Return a request-scoped ``RAGService`` wired to retrieval components."""
    return RAGService(
        retriever=retriever,
        context_builder=context_builder,
        prompt_builder=prompt_builder,
        settings=settings,
        advanced_pipeline=advanced_pipeline,
    )


# ============================================================================
# MCP Integration (Epic 03)
# ============================================================================


@lru_cache
def get_mcp_server_registry() -> McpServerRegistry:
    """Return app-scoped MCP server registry singleton.

    Process-wide registry for MCP server connections. Initialized once per
    app lifecycle with timeout settings from config.

    Phase 9: Updated to read timeout settings from Settings.
    """
    from app.ai.mcp.registry import McpServerRegistry as _McpServerRegistry

    settings = get_settings()
    return _McpServerRegistry(
        connection_timeout=float(settings.mcp_connection_timeout_seconds),
        tool_timeout=float(settings.mcp_tool_timeout_seconds),
    )


def get_mcp_permission_policy(
    settings: Settings = Depends(get_settings),
):
    """Return MCP permission policy for per-server/per-tool authorization.

    Composes with ToolAuthorizer (authenticated-only inherited). Both must pass
    for MCP tool execution.

    Phase 9: DI factory for MCP permission policy.
    """
    from app.ai.mcp.permissions import McpPermissionPolicy

    return McpPermissionPolicy(config=settings.mcp_permission_policy)


# ============================================================================
# Voice Interfaces (Epic 04)
# ============================================================================


def voice_config_from_settings(settings: Settings) -> "VoiceConfig":
    """Build a frozen :class:`VoiceConfig` from application settings."""
    from app.ai.voice.config import VoiceConfig

    return VoiceConfig.from_settings(settings)


@lru_cache
def get_interrupt_controller() -> "InterruptController":
    """Return the process-wide voice interrupt controller singleton."""
    from app.ai.voice.interrupt import InterruptController

    return InterruptController()


def _create_voice_adapter(settings: Settings) -> "OpenAiVoiceAdapter":
    from app.ai.voice.providers.openai_voice import OpenAiVoiceAdapter

    return OpenAiVoiceAdapter(
        api_key=settings.openai_api_key or "",
        config=voice_config_from_settings(settings),
    )


def get_stt_provider(settings: Settings = Depends(get_settings)) -> "SttProvider":
    """Return the configured STT provider (OpenAI Whisper when ``voice_provider=openai``)."""
    if settings.voice_provider != "openai":
        raise ValueError(f"Unsupported voice provider: {settings.voice_provider}")
    return _create_voice_adapter(settings)


def get_tts_provider(settings: Settings = Depends(get_settings)) -> "TtsProvider":
    """Return the configured TTS provider (OpenAI speech API when ``voice_provider=openai``)."""
    if settings.voice_provider != "openai":
        raise ValueError(f"Unsupported voice provider: {settings.voice_provider}")
    return _create_voice_adapter(settings)


def get_voice_session_manager(
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> "VoiceSessionManager":
    """Return a request-scoped voice session manager wired to chat ownership checks."""
    from app.ai.voice.session import VoiceSessionManager
    from app.db.chat import SqlChatStore

    return VoiceSessionManager(
        voice_config_from_settings(settings),
        SqlChatStore(session),
    )


# ============================================================================
# Memory System (Epic 05)
# ============================================================================


def get_memory_provider(
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_ai_settings),
) -> "PgVectorMemoryProvider":
    """Return a request-scoped pgvector-backed Memory provider."""
    from app.ai.memory.providers.pgvector import PgVectorMemoryProvider

    return PgVectorMemoryProvider(session=session, settings=settings)


def build_memory_manager(
    *,
    session: AsyncSession,
    settings: Settings,
    embedding_provider: EmbeddingProvider,
    prompt_manager: PromptManager,
) -> "MemoryManager":
    """Construct a ``MemoryManager`` bound to an already-resolved DB session.

    Shared by the ``get_memory_manager`` FastAPI dependency and by
    ``app/routers/chat.py`` chat-service wiring (Phase 8), so chat
    orchestration reuses the request's existing session instead of opening a
    second one via ``Depends(get_db_session)``.
    """
    from app.ai.memory.lifecycle_manager import LifecycleManager
    from app.ai.memory.manager import MemoryManager
    from app.ai.memory.project import ChatStoreSessionOwnershipChecker
    from app.ai.memory.providers.pgvector import PgVectorMemoryProvider
    from app.db.chat import SqlChatStore

    provider = PgVectorMemoryProvider(session=session, settings=settings)

    def background_provider_factory(db_session: AsyncSession) -> PgVectorMemoryProvider:
        return PgVectorMemoryProvider(session=db_session, settings=settings)

    return MemoryManager(
        provider=provider,
        settings=settings,
        embedding_provider=embedding_provider,
        prompt_manager=prompt_manager,
        lifecycle_manager=LifecycleManager(
            provider,
            settings=settings,
        ),
        background_provider_factory=background_provider_factory,
        session_ownership_checker=ChatStoreSessionOwnershipChecker(
            SqlChatStore(session)
        ),
    )


def get_memory_manager(
    settings: Settings = Depends(get_ai_settings),
    embedding_provider: EmbeddingProvider = Depends(get_embedding_provider),
    prompt_manager: PromptManager = Depends(get_prompt_manager),
    session: AsyncSession = Depends(get_db_session),
) -> "MemoryManager":
    """Return a request-scoped ``MemoryManager`` wired to the configured provider."""
    return build_memory_manager(
        session=session,
        settings=settings,
        embedding_provider=embedding_provider,
        prompt_manager=prompt_manager,
    )


def get_memory_context_builder(
    provider: "PgVectorMemoryProvider" = Depends(get_memory_provider),
    settings: Settings = Depends(get_ai_settings),
) -> "MemoryContextBuilder":
    """Return a request-scoped ``MemoryContextBuilder`` wired to the provider."""
    from app.ai.memory.context_builder import MemoryContextBuilder

    return MemoryContextBuilder(provider, settings=settings)


def get_semantic_retriever(
    provider: "PgVectorMemoryProvider" = Depends(get_memory_provider),
    settings: Settings = Depends(get_ai_settings),
    embedding_provider: EmbeddingProvider = Depends(get_embedding_provider),
    session: AsyncSession = Depends(get_db_session),
) -> "SemanticRetriever":
    """Return a request-scoped ``SemanticRetriever`` wired to the provider."""
    from app.ai.memory.project import ChatStoreSessionOwnershipChecker
    from app.ai.memory.semantic_retriever import SemanticRetriever
    from app.db.chat import SqlChatStore

    return SemanticRetriever(
        provider,
        embedding_provider,
        settings,
        session_ownership_checker=ChatStoreSessionOwnershipChecker(
            SqlChatStore(session)
        ),
    )


def get_conversation_summary_service(
    session: AsyncSession = Depends(get_db_session),
    prompt_manager: PromptManager = Depends(get_prompt_manager),
) -> "ConversationSummaryService":
    """Return a request-scoped summary façade over ``session_summaries``."""
    from app.ai.memory.summarizer import ConversationSummaryService
    from app.db.chat import SqlChatStore

    return ConversationSummaryService(
        chat_store=SqlChatStore(session),
        prompt_manager=prompt_manager,
    )


# ============================================================================
# Workflow Engine (Epic 06)
# ============================================================================


def get_workflow_store(
    session: AsyncSession = Depends(get_db_session),
    settings: Settings = Depends(get_ai_settings),
) -> "PostgresWorkflowStore":
    """Return a request-scoped postgres-backed Workflow store."""
    from app.ai.workflow.providers.postgres import PostgresWorkflowStore

    return PostgresWorkflowStore(session=session, settings=settings)


def get_workflow_manager(
    store: "PostgresWorkflowStore" = Depends(get_workflow_store),
    settings: Settings = Depends(get_ai_settings),
    tool_executor: ToolExecutor = Depends(get_tool_executor),
    prompt_manager: PromptManager = Depends(get_prompt_manager),
    agent_runtime: DefaultAgent = Depends(get_agent_runtime),
) -> "WorkflowManager":
    """Return a request-scoped ``WorkflowManager`` wired to the configured store.

    Task nodes execute through the same ``ToolExecutor`` used elsewhere; run
    execution is scheduled on a session dedicated to the background
    ``WorkflowExecutor`` task (Part I § Background execution), never the
    request-scoped ``session`` above.
    """
    from app.ai.workflow.providers.postgres import PostgresWorkflowStore

    def background_store_factory(session: AsyncSession) -> "PostgresWorkflowStore":
        return PostgresWorkflowStore(session=session, settings=settings)

    return _create_workflow_manager(
        store=store,
        settings=settings,
        tool_executor=tool_executor,
        prompt_manager=prompt_manager,
        agent_runtime=agent_runtime,
        background_store_factory=background_store_factory,
    )


def _create_workflow_manager(
    *,
    store: "PostgresWorkflowStore",
    settings: Settings,
    tool_executor: ToolExecutor,
    prompt_manager: PromptManager,
    agent_runtime: DefaultAgent,
    background_store_factory: Callable[[AsyncSession], "PostgresWorkflowStore"],
) -> "WorkflowManager":
    from app.ai.plugins.workflow.plugin_node import PluginNodeExecutor
    from app.ai.workflow.conditions.evaluator import ConditionEvaluator
    from app.ai.workflow.manager import WorkflowManager
    from app.ai.workflow.models import NodeType
    from app.ai.workflow.nodes.base import NodeExecutor
    from app.ai.workflow.nodes.approval_node import ApprovalNodeExecutor
    from app.ai.workflow.nodes.agent_node import AgentNodeExecutor
    from app.ai.workflow.nodes.llm_node import LLMNodeExecutor
    from app.ai.workflow.nodes.parallel_node import ForkNodeExecutor, JoinNodeExecutor
    from app.ai.workflow.nodes.router_node import RouterNodeExecutor
    from app.ai.workflow.nodes.task_node import TaskNodeExecutor

    workflow_plugin_registry = get_workflow_plugin_registry()
    node_executors: dict[NodeType, NodeExecutor] = {
        NodeType.TASK: TaskNodeExecutor(tool_executor),
        NodeType.LLM: LLMNodeExecutor(
            prompt_manager=prompt_manager,
            settings=settings,
        ),
        NodeType.AGENT: AgentNodeExecutor(agent_runtime, settings=settings),
        NodeType.ROUTER: RouterNodeExecutor(ConditionEvaluator()),
        NodeType.FORK: ForkNodeExecutor(
            max_parallel_branches=settings.workflow_max_parallel_branches
        ),
        NodeType.JOIN: JoinNodeExecutor(),
        NodeType.APPROVAL: ApprovalNodeExecutor(),
    }
    if settings.plugins_enabled:
        node_executors[NodeType.PLUGIN] = PluginNodeExecutor(
            workflow_plugin_registry=workflow_plugin_registry,
            settings=settings,
        )

    return WorkflowManager(
        store=store,
        settings=settings,
        node_executors=node_executors,
        background_store_factory=background_store_factory,
        tool_registry=get_tool_registry(),
        plugin_registry=get_plugin_registry(),
        workflow_plugin_registry=workflow_plugin_registry,
    )


def build_workflow_manager_for_session(
    session: AsyncSession,
    settings: Settings,
) -> "WorkflowManager":
    """Build a ``WorkflowManager`` for a standalone DB session (tool calls)."""
    from app.ai.workflow.providers.postgres import PostgresWorkflowStore

    registry = get_tool_registry()
    prompt_manager = get_prompt_manager()
    tool_executor = _create_tool_executor(registry=registry, settings=settings)
    agent_runtime = create_default_agent(
        settings=settings,
        tool_registry=registry,
        prompt_manager=prompt_manager,
        tool_executor=tool_executor,
    )
    store = PostgresWorkflowStore(session=session, settings=settings)

    def background_store_factory(
        bg_session: AsyncSession,
    ) -> "PostgresWorkflowStore":
        return PostgresWorkflowStore(session=bg_session, settings=settings)

    return _create_workflow_manager(
        store=store,
        settings=settings,
        tool_executor=tool_executor,
        prompt_manager=prompt_manager,
        agent_runtime=agent_runtime,
        background_store_factory=background_store_factory,
    )


def build_agent_approval_service_for_session(
    session: AsyncSession,
    settings: Settings,
    *,
    scratchpad_store: "ScratchpadStore | None" = None,
) -> "AgentApprovalService":
    """Build an approval orchestrator for a standalone DB session."""
    from app.ai.agent.scratchpad.store import ScratchpadStore
    from app.ai.hitl.service import AgentApprovalService
    from app.ai.hitl.store import AgentToolApprovalStore
    from app.db.chat import SqlChatStore

    registry = get_tool_registry()
    return AgentApprovalService(
        approval_store=AgentToolApprovalStore(
            session,
            client_audit_retention_days=settings.hitl_client_audit_retention_days,
        ),
        chat_store=SqlChatStore(session),
        tool_registry=registry,
        tool_executor=_create_tool_executor(registry=registry, settings=settings),
        scratchpad_store=scratchpad_store or ScratchpadStore(),
        approval_timeout_hours=settings.hitl_approval_timeout_hours,
        default_model=settings.default_llm_model(),
        notification_dispatcher=get_hitl_notification_dispatcher(),
    )


def build_hitl_resume_executor(
    settings: Settings,
    *,
    approval_service: "AgentApprovalService | None" = None,
    scratchpad_store: "ScratchpadStore | None" = None,
) -> "AgentExecutor":
    """Build an ``AgentExecutor`` for background orphan-resume jobs."""
    from app.ai.agent.executor.agent_executor import AgentExecutor
    from app.ai.agent.executor.tool_runner import ToolRunner
    from app.ai.agent.planner.react_planner import ReActPlanner
    from app.ai.agent.scratchpad import ScratchpadStore
    from app.ai.agent.streaming import NoOpStreamPublisher
    from app.ai.hitl.policy import ApprovalPolicy
    from app.providers.factory import ProviderFactory

    registry = get_tool_registry()
    prompt_manager = get_prompt_manager()
    tool_executor = _create_tool_executor(registry=registry, settings=settings)
    shared_scratchpad_store = scratchpad_store or ScratchpadStore()
    if approval_service is not None:
        approval_service._scratchpad_store = shared_scratchpad_store
    provider = ProviderFactory.get_provider(settings.llm_provider, settings)
    approval_policy = (
        ApprovalPolicy(required_tool_names=frozenset(settings.hitl_required_tool_names))
        if settings.hitl_enabled
        else None
    )
    runner = ToolRunner(
        tool_executor=tool_executor,
        tool_registry=registry,
        stream_publisher=NoOpStreamPublisher(),
        hitl_enabled=settings.hitl_enabled,
        approval_policy=approval_policy,
        approval_service=approval_service,
    )
    return AgentExecutor(
        planner=ReActPlanner(
            provider=provider,
            tool_registry=registry,
            prompt_manager=prompt_manager,
            scratchpad_store=shared_scratchpad_store,
        ),
        provider=provider,
        tool_runner=runner,
        stream_publisher=NoOpStreamPublisher(),
        scratchpad_store=shared_scratchpad_store,
        prompt_manager=prompt_manager,
    )


async def reconcile_workflow_runs_at_startup(settings: Settings) -> int:
    """Reattach executors to orphaned ``running`` runs after process restart."""
    from app.ai.workflow.providers.postgres import PostgresWorkflowStore
    from app.db.engine import get_sessionmaker

    registry = get_tool_registry()
    prompt_manager = get_prompt_manager()
    tool_executor = _create_tool_executor(registry=registry, settings=settings)
    agent_runtime = create_default_agent(
        settings=settings,
        tool_registry=registry,
        prompt_manager=prompt_manager,
        tool_executor=tool_executor,
    )

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        store = PostgresWorkflowStore(session=session, settings=settings)

        def background_store_factory(
            bg_session: AsyncSession,
        ) -> "PostgresWorkflowStore":
            return PostgresWorkflowStore(session=bg_session, settings=settings)

        manager = _create_workflow_manager(
            store=store,
            settings=settings,
            tool_executor=tool_executor,
            prompt_manager=prompt_manager,
            agent_runtime=agent_runtime,
            background_store_factory=background_store_factory,
        )
        return await manager.reconcile_orphaned_runs()


# ============================================================================
# Observability (Epic 07)
# ============================================================================


def get_usage_aggregator(
    session: AsyncSession = Depends(get_db_session),
) -> "UsageAggregator":
    """Return a request-scoped usage/cost aggregator over ``usage_events``."""
    from app.ai.observability.aggregation.usage_aggregator import UsageAggregator

    return UsageAggregator(session)
