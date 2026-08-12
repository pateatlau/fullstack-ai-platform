from functools import lru_cache
from typing import Any, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_VERSION = "0.1.0"
_INSECURE_DEV_JWT_SECRET = "dev-insecure-jwt-secret-change-me"
_DEFAULT_DATABASE_URL = "postgresql+asyncpg://chatbot:chatbot@localhost:5433/chatbot"
_LEGACY_DEFAULT_DATABASE_URL = (
    "postgresql+asyncpg://chatbot:chatbot@localhost:5432/chatbot"
)
_REJECTED_DEFAULT_DATABASE_URLS = frozenset(
    {_DEFAULT_DATABASE_URL, _LEGACY_DEFAULT_DATABASE_URL}
)

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class Settings(BaseSettings):
    """Env-driven application configuration.

    Values are read from environment variables (or a local `.env` file in
    development). See `backend-python/.env.example` for the full list.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_provider: str = "openai"

    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"

    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.1-flash-lite"

    groq_api_key: str | None = None
    groq_model: str = "openai/gpt-oss-20b"

    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-haiku-4-5-20251001"

    cors_allowed_origins: str = "http://localhost:5173"

    database_url: str = _DEFAULT_DATABASE_URL

    # Google OAuth 2.0 (ID-token verification). Required to serve /api/auth/google.
    google_client_id: str | None = None

    # App-issued JWT (plan Section 3.2). The secret must be overridden outside
    # local development; production values come from environment/secret stores.
    jwt_secret: str = _INSECURE_DEV_JWT_SECRET
    jwt_algorithm: str = "HS256"
    jwt_access_token_expires_minutes: int = 60

    # Guest quota (plan Section 12): config-driven daily message ceiling for
    # anonymous callers. Authenticated users are not governed by this limit.
    guest_daily_message_quota: int = 20

    # V1.1.1 public demo protection (Phase 1): cap guest completion length.
    guest_max_output_tokens: int = Field(default=4096, ge=1)
    # Daily document upload count (auth-only upload path). ``None`` disables quota.
    authenticated_daily_upload_quota: int | None = Field(default=None, ge=1)
    # Future-proof if guest upload is ever enabled.
    guest_daily_upload_quota: int = Field(default=5, ge=1)
    # When true, tighten demo caps for public deploy (see effective_* helpers).
    demo_mode_strict: bool = False

    # Feature flag (plan Section 13, Phase 5 mitigation): when disabled, chat
    # endpoints behave statelessly (no DB reads/writes), preserving the original
    # request/response contracts exactly.
    chat_persistence_enabled: bool = True

    # Summarization trigger (plan Sections 5.5, 14.3): create a new session
    # summary once this many messages accumulate past the last summary boundary.
    summary_trigger_message_count: int = 20

    app_env: str = "development"
    max_message_length: int = 4000
    request_timeout_seconds: int = 30
    request_body_limit_bytes: int = Field(default=16 * 1024, ge=1)
    log_level: LogLevel = "INFO"

    # HTTP rate limiting (Phase 5 middleware; per-minute sliding window).
    rate_limit_anonymous_per_minute: int = Field(default=30, ge=1)
    rate_limit_authenticated_per_minute: int = Field(default=120, ge=1)

    # AI / RAG configuration matrix (Phase 1). Feature flags default off so
    # MVP chat/auth/persistence behave identically until later phases enable
    # RAG and tool endpoints.
    embedding_provider: str = "openai"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = Field(default=1536, ge=1)
    embedding_batch_size: int = Field(default=100, ge=1)
    chunk_size: int = Field(default=1000, ge=1)
    chunk_overlap: int = Field(default=200, ge=0)
    # V2 Epic 2 (Phase 2): parent-child chunking (honoured when advanced RAG on).
    child_chunk_size: int = Field(default=400, ge=1)
    child_chunk_overlap: int = Field(default=80, ge=0)
    parent_chunk_size: int = Field(default=2000, ge=1)
    parent_chunk_overlap: int = Field(default=200, ge=0)
    # V2 Epic 2 (Phase 4): hybrid dense + lexical retrieval (RRF fusion).
    hybrid_dense_top_k: int = Field(default=20, ge=1)
    hybrid_lexical_top_k: int = Field(default=20, ge=1)
    rrf_k: int = Field(default=60, ge=1)
    # V2 Epic 2 (Phase 5): LLM query rewrite (honoured only when advanced flag on).
    query_rewrite_enabled: bool = True
    # V2 Epic 2 (Phase 6): cross-encoder rerank (honoured only when advanced flag on).
    cohere_api_key: str | None = None
    rerank_provider: str = "cohere"
    rerank_model: str = "rerank-v3.5"
    rerank_timeout_ms: int = Field(default=1500, ge=1)
    rag_top_k: int = Field(default=5, ge=1)
    rag_default_prompt_template: str = "rag/answer/v1"
    rag_context_max_chars: int = Field(default=8000, ge=1)
    # V2 Epic 2 (Phase 8): bounded original-text excerpt on Citation.snippet.
    citation_snippet_max_chars: int = Field(default=240, ge=1)
    rag_enabled: bool = False
    tools_enabled: bool = False
    # When false, ``POST /api/chat/stream`` returns 503 ``feature_disabled`` and
    # clients should use non-streaming ``POST /api/chat`` instead.
    chat_streaming_enabled: bool = True
    default_temperature: float = 0.7
    default_max_tokens: int | None = None
    document_upload_max_bytes: int = Field(default=10_485_760, ge=1)
    web_search_provider: str = "tavily"
    web_search_api_key: str | None = None
    web_search_max_results: int = Field(default=5, ge=1)

    # V2 Epic 1 (Phase 1): agent runtime feature flag. When disabled, chat
    # endpoints keep the V1.1 orchestration path unchanged.
    agent_runtime_enabled: bool = False

    # V2 Epic 2: master advanced RAG flag (default false). When true: ingest
    # uses ParentChildChunker; chat/RAG use AdvancedRetrievalPipeline.
    # Flag-off keeps V1 RecursiveChunker + dense Retriever path.
    advanced_rag_enabled: bool = False

    # V2 Epic 3: MCP integration flag (default false). When true: connect to
    # configured MCP servers; discover and register remote tools; execute MCP
    # tool calls via stdio transport. Flag-off keeps V1 local tools unchanged.
    mcp_enabled: bool = False

    # V2 Epic 3 Phase 7: MCP permission policy (per-server/per-tool allowlists).
    # Empty dict → all configured servers/tools allowed.
    # Example: {"allowed_servers": ["filesystem"], "allowed_tools": {"filesystem": ["*"]}}
    mcp_permission_policy: dict[str, Any] = Field(default_factory=dict)

    # V2 Epic 3 Phase 8: MCP server configurations and timeouts.
    # List of MCP server connection configs (name, command, args, env, transport).
    mcp_servers: list[dict[str, Any]] = Field(default_factory=list)
    mcp_connection_timeout_seconds: int = Field(default=10, ge=1)
    mcp_tool_timeout_seconds: int = Field(default=30, ge=1)

    # V2 Epic 4: Voice interfaces flag (default false). When true: enable voice
    # STT/TTS pipelines, WebSocket voice endpoint, and voice mode UI for auth users.
    # Flag-off keeps text chat SSE unchanged.
    voice_enabled: bool = False

    # V2 Epic 4: Voice provider and model configuration.
    voice_provider: str = "openai"
    voice_stt_model: str = "whisper-1"
    voice_tts_model: str = "tts-1"  # Prefer tts-1 over tts-1-hd for lower latency.
    voice_tts_voice: str = "alloy"

    # V2 Epic 4: Voice audio format and streaming configuration.
    voice_sample_rate_hz: int = Field(default=24000, ge=1)
    voice_audio_encoding: str = "pcm16"
    voice_max_chunk_bytes: int = Field(default=4096, ge=1)

    # V2 Epic 4: TTS flush tuning (lower time-to-first-audio during voice replies).
    voice_tts_early_flush_chars: int = Field(default=40, ge=1)
    voice_tts_time_flush_ms: int = Field(default=500, ge=1)
    voice_tts_min_time_flush_chars: int = Field(default=12, ge=1)

    # V2 Epic 4: Voice session lifecycle and timeout configuration.
    voice_session_timeout_seconds: int = Field(default=300, ge=1)
    voice_heartbeat_interval_seconds: int = Field(default=30, ge=1)
    voice_max_utterance_seconds: int = Field(default=60, ge=1)

    # V2 Epic 5 Phase 1: Memory subsystem infrastructure flag (default false).
    # When true: validates memory provider config and registers Memory DI wiring;
    # domain models, provider scaffold, and DB tables are present. Chat
    # retrieval/injection, persistence, REST API, and Settings UI are not wired
    # until later phases. Flag-off keeps chat/RAG/voice/MCP/agent paths unchanged.
    memory_enabled: bool = False

    # V2 Epic 5: Memory provider and retrieval/quality tuning.
    memory_provider: str = "pgvector"
    memory_retrieval_top_k: int = Field(default=8, ge=1)
    memory_min_quality_score: float = Field(default=0.4, ge=0.0, le=1.0)
    memory_min_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    memory_dedupe_similarity_threshold: float = Field(default=0.92, ge=0.0, le=1.0)
    # Token budget for the injected memory block (caps prompt growth from memory context).
    memory_token_budget: int = Field(default=1500, ge=1)
    memory_extraction_enabled: bool = True
    # Empty string means: use the same model as the originating chat turn.
    memory_extraction_model: str = ""
    memory_archived_retention_days: int = Field(default=90, ge=1)

    # V2 Epic 6 Phase 1: Workflow Engine infrastructure flag (default false).
    # When true: validates workflow provider config and registers Workflow DI
    # wiring; domain models, provider scaffold, and DB tables are present.
    # Graph validation, execution engine, REST API, and frontend are not wired
    # until later phases. Flag-off keeps chat/RAG/voice/MCP/memory/agent paths
    # unchanged.
    workflow_engine_enabled: bool = False

    # V2 Epic 6: Workflow provider and execution tuning.
    workflow_provider: str = "postgres"
    workflow_max_nodes_per_definition: int = Field(default=50, ge=1)
    workflow_max_parallel_branches: int = Field(default=8, ge=1)
    workflow_node_timeout_seconds: int = Field(default=120, ge=1)
    workflow_max_node_retries: int = Field(default=3, ge=0)
    workflow_node_retry_base_delay_seconds: float = Field(default=1.0, ge=0.0)
    workflow_max_run_duration_minutes: int = Field(default=60, ge=1)
    workflow_approval_timeout_hours: int = Field(default=0, ge=0)
    workflow_run_retention_days: int = Field(default=90, ge=1)

    # V2 Epic 7 Phase 1: Observability infrastructure flag (default false).
    # When true: registers OTel TracerProvider/MeterProvider, span helpers, and
    # trace/span-id log correlation. Pipeline instrumentation, cost accounting,
    # REST API, and evaluation extensions are not wired until later phases.
    # Flag-off keeps all Epic 06 pipeline paths unchanged.
    observability_enabled: bool = False

    # V2 Epic 8 Phase 1: Plugin architecture flag (default false).
    # When true: discover and load plugins from configured directories at startup.
    # Tool/prompt/workflow/MCP registry wiring and REST API follow in later phases.
    # Flag-off keeps all Epic 07 pipeline paths unchanged.
    plugins_enabled: bool = False
    plugin_directories: list[str] = Field(default_factory=lambda: ["plugins"])
    plugin_allowlist: list[str] = Field(default_factory=list)
    # Cooperative loader wait for register(registrar) in a daemon thread; does not
    # interrupt in-process plugin code (see Epic 08 § Registration timeout).
    plugin_registration_wait_timeout_seconds: int = Field(default=30, ge=1)

    # V2 Epic 9 Phase 1: Human-in-the-loop infrastructure flag (default false).
    # When true: ApprovalPolicy gates flagged tool calls on agent and workflow
    # surfaces; REST inbox/decide endpoints and resume wiring follow in later
    # phases. Flag-off keeps all Epic 08 pipeline paths unchanged.
    hitl_enabled: bool = False
    hitl_required_tool_names: list[str] = Field(default_factory=list)
    # 0 disables expiration; a pending approval past its timeout is lazily
    # transitioned to ``expired`` the next time it is read/decided/revised.
    hitl_approval_timeout_hours: int = Field(default=0, ge=0)
    hitl_max_reason_length: int = Field(default=2000, ge=1)
    hitl_max_comment_length: int = Field(default=2000, ge=1)
    # Pending-only client audit fields (``source_ip``, ``client_metadata``) are
    # redacted on terminal transitions and purged after this many days even if
    # the approval is still pending. ``0`` disables time-based purge.
    hitl_client_audit_retention_days: int = Field(default=90, ge=0)
    # When true, resolve ``source_ip`` from the leftmost ``X-Forwarded-For``
    # hop; otherwise use the direct ASGI client address.
    hitl_trust_forwarded_client_ip: bool = False
    hitl_max_user_agent_length: int = Field(default=512, ge=1)

    # V2 Epic 10 Phase 1: Background Jobs infrastructure flag (default false).
    # When true: JobWorker and JobScheduler start in app lifespan; queue-backed
    # handlers enforce deferred HITL/workflow/RAG/eval work. Flag-off keeps all
    # Epic 09 pipeline paths unchanged.
    background_jobs_enabled: bool = False
    background_jobs_worker_poll_interval_seconds: int = Field(default=5, ge=1)
    background_jobs_worker_batch_size: int = Field(default=10, ge=1)
    background_jobs_claim_lease_seconds: int = Field(default=900, ge=1)
    background_jobs_handler_timeout_seconds: int = Field(default=600, ge=1)
    background_jobs_default_max_attempts: int = Field(default=3, ge=1)
    background_jobs_retry_base_delay_seconds: float = Field(default=5.0, ge=0.0)
    background_jobs_retry_max_delay_seconds: float = Field(default=300.0, ge=0.0)
    background_jobs_scheduler_poll_interval_seconds: int = Field(default=30, ge=1)
    background_jobs_retention_days: int = Field(default=30, ge=1)

    # Rule-based approval policy (recommendation #1): an ordered list of rule
    # dicts evaluated against tool name/category/risk/caller/environment/
    # arguments. Empty list preserves the legacy ``requires_approval`` /
    # ``hitl_required_tool_names`` gating exactly. See ``app/ai/hitl/rules.py``
    # for the condition/outcome schema.
    hitl_policy_rules: list[dict[str, Any]] = Field(default_factory=list)

    # Outbound approval notifications (recommendation #6). Providers fire
    # best-effort (failures are logged, never raised) alongside the existing
    # SSE/inbox notification path.
    hitl_notification_providers: list[str] = Field(default_factory=list)
    hitl_notification_webhook_url: str | None = None
    hitl_notification_slack_webhook_url: str | None = None
    hitl_notification_teams_webhook_url: str | None = None
    hitl_notification_discord_webhook_url: str | None = None
    hitl_notification_timeout_seconds: float = Field(default=5.0, gt=0.0)

    # OpenTelemetry configuration (honoured only when OBSERVABILITY_ENABLED=true).
    otel_service_name: str = "fullstack-ai-platform"
    # Empty string selects the console span exporter (dev default).
    otel_exporter_otlp_endpoint: str = ""
    # Dev-safe default — override per environment (staging 0.25, production 0.05).
    otel_traces_sample_ratio: float = Field(default=1.0, ge=0.0, le=1.0)

    # Cost accounting (honoured only when OBSERVABILITY_ENABLED=true).
    observability_cost_pricing_file: str = "config/model_pricing.yaml"
    observability_cost_pricing_version: str = "2026-08"

    # Evaluation regression tolerances (Phase 8).
    observability_regression_pass_rate_tolerance_pct: float = Field(default=5.0, ge=0.0)
    observability_regression_latency_tolerance_pct: float = Field(default=20.0, ge=0.0)
    observability_regression_latency_floor_ms: float = Field(default=10.0, ge=0.0)

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("LOG_LEVEL must be a string.")
        normalized = value.strip().upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
            raise ValueError(
                f"LOG_LEVEL must be one of DEBUG, INFO, WARNING, ERROR; got '{value}'."
            )
        return normalized

    @field_validator("default_max_tokens", mode="before")
    @classmethod
    def normalize_default_max_tokens(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("authenticated_daily_upload_quota", mode="before")
    @classmethod
    def normalize_authenticated_daily_upload_quota(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def effective_guest_max_output_tokens(self) -> int:
        """Guest completion cap; ``demo_mode_strict`` lowers to 512 max."""
        if self.demo_mode_strict:
            return min(self.guest_max_output_tokens, 512)
        return self.guest_max_output_tokens

    @property
    def effective_authenticated_daily_upload_quota(self) -> int | None:
        """Daily upload cap for signed-in users; strict demo defaults to 20."""
        if self.demo_mode_strict:
            configured = self.authenticated_daily_upload_quota
            return configured if configured is not None else 20
        return self.authenticated_daily_upload_quota

    @property
    def is_development(self) -> bool:
        return self.app_env.strip().lower() == "development"

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.cors_allowed_origins.split(",")
            if origin.strip()
        ]

    def request_body_limit_message(self) -> str:
        limit = self.request_body_limit_bytes
        return (
            f"Request body exceeds the {limit} byte limit. "
            "Reduce message size and retry."
        )

    def document_upload_limit_message(self) -> str:
        limit = self.document_upload_max_bytes
        return (
            f"Document upload exceeds the {limit} byte limit. "
            "Reduce file size and retry."
        )

    def validate_provider_key(self) -> None:
        """Fail fast if the selected provider's API key is missing."""
        supported_providers = {"openai", "gemini", "groq", "anthropic"}
        if self.llm_provider not in supported_providers:
            supported = ", ".join(sorted(supported_providers))
            raise ValueError(
                f"Unsupported LLM_PROVIDER '{self.llm_provider}'. "
                f"Supported providers: {supported}."
            )

        if self.llm_provider == "openai" and not self.openai_api_key:
            raise ValueError(
                "LLM_PROVIDER is 'openai' but OPENAI_API_KEY is not set. "
                "Set it in backend-python/.env (see .env.example)."
            )
        if self.llm_provider == "gemini" and not self.gemini_api_key:
            raise ValueError(
                "LLM_PROVIDER is 'gemini' but GEMINI_API_KEY is not set. "
                "Set it in backend-python/.env (see .env.example)."
            )
        if self.llm_provider == "groq" and not self.groq_api_key:
            raise ValueError(
                "LLM_PROVIDER is 'groq' but GROQ_API_KEY is not set. "
                "Set it in backend-python/.env (see .env.example)."
            )
        if self.llm_provider == "anthropic" and not self.anthropic_api_key:
            raise ValueError(
                "LLM_PROVIDER is 'anthropic' but ANTHROPIC_API_KEY is not set. "
                "Set it in backend-python/.env (see .env.example)."
            )

    def validate_production_requirements(self) -> None:
        """Fail fast on missing or insecure settings outside development."""
        if self.is_development:
            return

        errors: list[str] = []

        if self.jwt_secret == _INSECURE_DEV_JWT_SECRET:
            errors.append(
                "JWT_SECRET must be explicitly set when APP_ENV is not 'development'."
            )

        if self.database_url in _REJECTED_DEFAULT_DATABASE_URLS:
            errors.append(
                "DATABASE_URL must be explicitly set when APP_ENV is not 'development'."
            )

        if not self.google_client_id or not self.google_client_id.strip():
            errors.append(
                "GOOGLE_CLIENT_ID must be set when APP_ENV is not 'development' "
                "(auth routes are enabled)."
            )

        if errors:
            raise ValueError(" ".join(errors))

    def validate_rag_requirements(self) -> None:
        """Fail fast when RAG is enabled but embedding configuration is invalid."""
        if not self.rag_enabled:
            return

        supported_embedding_providers = {"openai"}
        if self.embedding_provider not in supported_embedding_providers:
            supported = ", ".join(sorted(supported_embedding_providers))
            raise ValueError(
                f"Unsupported EMBEDDING_PROVIDER '{self.embedding_provider}'. "
                f"Supported providers: {supported}."
            )

        if self.embedding_provider == "openai" and not self.openai_api_key:
            raise ValueError(
                "RAG_ENABLED is true but OPENAI_API_KEY is not set "
                "(required when EMBEDDING_PROVIDER=openai). "
                "Set it in backend-python/.env (see .env.example)."
            )

        if self.chunk_overlap >= self.chunk_size:
            raise ValueError(
                "CHUNK_OVERLAP must be less than CHUNK_SIZE when RAG_ENABLED is true. "
                f"Got CHUNK_OVERLAP={self.chunk_overlap}, CHUNK_SIZE={self.chunk_size}."
            )

    def validate_advanced_rag_requirements(self) -> None:
        """Fail fast when advanced RAG chunk sizing is invalid."""
        if not self.advanced_rag_enabled:
            return

        if self.child_chunk_overlap >= self.child_chunk_size:
            raise ValueError(
                "CHILD_CHUNK_OVERLAP must be less than CHILD_CHUNK_SIZE when "
                "ADVANCED_RAG_ENABLED is true. "
                f"Got CHILD_CHUNK_OVERLAP={self.child_chunk_overlap}, "
                f"CHILD_CHUNK_SIZE={self.child_chunk_size}."
            )
        if self.parent_chunk_overlap >= self.parent_chunk_size:
            raise ValueError(
                "PARENT_CHUNK_OVERLAP must be less than PARENT_CHUNK_SIZE when "
                "ADVANCED_RAG_ENABLED is true. "
                f"Got PARENT_CHUNK_OVERLAP={self.parent_chunk_overlap}, "
                f"PARENT_CHUNK_SIZE={self.parent_chunk_size}."
            )

    def validate_tools_requirements(self) -> None:
        """Fail fast when tools are enabled but web search is not configured."""
        if not self.tools_enabled:
            return

        supported_search_providers = {"tavily"}
        if self.web_search_provider not in supported_search_providers:
            supported = ", ".join(sorted(supported_search_providers))
            raise ValueError(
                f"Unsupported WEB_SEARCH_PROVIDER '{self.web_search_provider}'. "
                f"Supported providers: {supported}."
            )

        if not self.web_search_api_key:
            raise ValueError(
                "TOOLS_ENABLED is true but WEB_SEARCH_API_KEY is not set. "
                "Set it in backend-python/.env (see .env.example)."
            )

    def validate_voice_requirements(self) -> None:
        """Fail fast when voice is enabled but configuration is invalid."""
        if not self.voice_enabled:
            return

        supported_voice_providers = {"openai"}
        if self.voice_provider not in supported_voice_providers:
            supported = ", ".join(sorted(supported_voice_providers))
            raise ValueError(
                f"Unsupported VOICE_PROVIDER '{self.voice_provider}'. "
                f"Supported providers: {supported}."
            )

        supported_audio_encodings = {"pcm16"}
        if self.voice_audio_encoding not in supported_audio_encodings:
            supported = ", ".join(sorted(supported_audio_encodings))
            raise ValueError(
                f"Unsupported VOICE_AUDIO_ENCODING '{self.voice_audio_encoding}'. "
                f"Supported encodings: {supported}."
            )

        if self.voice_provider == "openai" and not self.openai_api_key:
            raise ValueError(
                "VOICE_ENABLED is true with VOICE_PROVIDER='openai' but "
                "OPENAI_API_KEY is not set. "
                "Set it in backend-python/.env (see .env.example)."
            )

        if self.voice_heartbeat_interval_seconds >= self.voice_session_timeout_seconds:
            raise ValueError(
                "VOICE_HEARTBEAT_INTERVAL_SECONDS must be less than "
                "VOICE_SESSION_TIMEOUT_SECONDS when VOICE_ENABLED is true. "
                f"Got VOICE_HEARTBEAT_INTERVAL_SECONDS={self.voice_heartbeat_interval_seconds}, "
                f"VOICE_SESSION_TIMEOUT_SECONDS={self.voice_session_timeout_seconds}."
            )

    def validate_memory_requirements(self) -> None:
        """Fail fast when Memory is enabled but configuration is invalid."""
        if not self.memory_enabled:
            return

        supported_memory_providers = {"pgvector"}
        if self.memory_provider not in supported_memory_providers:
            supported = ", ".join(sorted(supported_memory_providers))
            raise ValueError(
                f"Unsupported MEMORY_PROVIDER '{self.memory_provider}'. "
                f"Supported providers: {supported}."
            )

        supported_embedding_providers = {"openai"}
        if self.embedding_provider not in supported_embedding_providers:
            supported = ", ".join(sorted(supported_embedding_providers))
            raise ValueError(
                f"Unsupported EMBEDDING_PROVIDER '{self.embedding_provider}'. "
                f"Supported providers: {supported}."
            )

        if self.embedding_provider == "openai" and not self.openai_api_key:
            raise ValueError(
                "MEMORY_ENABLED is true but OPENAI_API_KEY is not set "
                "(required when EMBEDDING_PROVIDER=openai). "
                "Set it in backend-python/.env (see .env.example)."
            )

    def validate_hitl_requirements(self) -> None:
        """Fail fast when a configured notification provider has no target URL."""
        if not self.hitl_notification_providers:
            return

        supported_providers = {
            "webhook",
            "slack",
            "teams",
            "discord",
            "in_app",
        }
        seen: set[str] = set()
        for provider in self.hitl_notification_providers:
            if provider in seen:
                raise ValueError(
                    f"HITL_NOTIFICATION_PROVIDERS contains duplicate entry "
                    f"'{provider}'."
                )
            seen.add(provider)

        provider_url_settings = {
            "webhook": self.hitl_notification_webhook_url,
            "slack": self.hitl_notification_slack_webhook_url,
            "teams": self.hitl_notification_teams_webhook_url,
            "discord": self.hitl_notification_discord_webhook_url,
        }
        for provider in self.hitl_notification_providers:
            if provider not in supported_providers:
                supported = ", ".join(sorted(supported_providers))
                raise ValueError(
                    f"Unsupported HITL_NOTIFICATION_PROVIDERS entry '{provider}'. "
                    f"Supported providers: {supported}."
                )
            required_url = provider_url_settings.get(provider)
            if provider in provider_url_settings and not required_url:
                raise ValueError(
                    f"HITL_NOTIFICATION_PROVIDERS includes '{provider}' but its "
                    f"webhook URL setting is not configured."
                )

    def validate_workflow_requirements(self) -> None:
        """Fail fast when Workflow Engine is enabled but configuration is invalid."""
        if not self.workflow_engine_enabled:
            return

        supported_workflow_providers = {"postgres"}
        if self.workflow_provider not in supported_workflow_providers:
            supported = ", ".join(sorted(supported_workflow_providers))
            raise ValueError(
                f"Unsupported WORKFLOW_PROVIDER '{self.workflow_provider}'. "
                f"Supported providers: {supported}."
            )

    def validate_background_jobs_requirements(self) -> None:
        """Fail fast when Background Jobs is enabled but configuration is invalid."""
        if not self.background_jobs_enabled:
            return

        if (
            self.background_jobs_handler_timeout_seconds
            >= self.background_jobs_claim_lease_seconds
        ):
            raise ValueError(
                "BACKGROUND_JOBS_HANDLER_TIMEOUT_SECONDS must be strictly less than "
                "BACKGROUND_JOBS_CLAIM_LEASE_SECONDS so in-flight handlers are not "
                "reclaimed before they can finish."
            )

    def log_development_warnings(self, logger: object) -> None:
        """Emit human-readable warnings for permissive development defaults."""
        if not self.is_development:
            return

        warn = getattr(logger, "warning", None)
        if not callable(warn):
            return

        if self.jwt_secret == _INSECURE_DEV_JWT_SECRET:
            warn(
                "Using default JWT_SECRET; override before deploying outside "
                "development."
            )

        if not self.google_client_id or not self.google_client_id.strip():
            warn(
                "GOOGLE_CLIENT_ID is not set; POST /api/auth/google will return "
                "auth_not_configured."
            )

        if self.database_url in _REJECTED_DEFAULT_DATABASE_URLS:
            warn(
                "Using default DATABASE_URL (localhost postgres); ensure postgres "
                "is running when persistence or auth is used."
            )

    def validate_startup(self) -> None:
        self.validate_provider_key()
        self.validate_rag_requirements()
        self.validate_advanced_rag_requirements()
        self.validate_tools_requirements()
        self.validate_voice_requirements()
        self.validate_memory_requirements()
        self.validate_workflow_requirements()
        self.validate_hitl_requirements()
        self.validate_background_jobs_requirements()
        self.validate_production_requirements()


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_startup()
    return settings
