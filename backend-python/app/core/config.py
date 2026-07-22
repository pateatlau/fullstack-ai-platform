from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_VERSION = "0.1.0"
_INSECURE_DEV_JWT_SECRET = "dev-insecure-jwt-secret-change-me"
_DEFAULT_DATABASE_URL = "postgresql+asyncpg://chatbot:chatbot@localhost:5432/chatbot"

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
    rag_top_k: int = Field(default=5, ge=1)
    rag_default_prompt_template: str = "rag/answer/v1"
    rag_context_max_chars: int = Field(default=8000, ge=1)
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

        if self.database_url == _DEFAULT_DATABASE_URL:
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

        if self.database_url == _DEFAULT_DATABASE_URL:
            warn(
                "Using default DATABASE_URL (localhost postgres); ensure postgres "
                "is running when persistence or auth is used."
            )

    def validate_startup(self) -> None:
        self.validate_provider_key()
        self.validate_rag_requirements()
        self.validate_tools_requirements()
        self.validate_production_requirements()


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_startup()
    return settings
