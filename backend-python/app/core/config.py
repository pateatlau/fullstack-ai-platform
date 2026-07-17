from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

APP_VERSION = "0.1.0"


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

    database_url: str = "postgresql+asyncpg://chatbot:chatbot@localhost:5432/chatbot"

    # Google OAuth 2.0 (ID-token verification). Required to serve /api/auth/google.
    google_client_id: str | None = None

    # App-issued JWT (plan Section 3.2). The secret must be overridden outside
    # local development; production values come from environment/secret stores.
    jwt_secret: str = "dev-insecure-jwt-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expires_minutes: int = 60

    # Guest quota (plan Section 12): config-driven daily message ceiling for
    # anonymous callers. Authenticated users are not governed by this limit.
    guest_daily_message_quota: int = 20

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

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.cors_allowed_origins.split(",")
            if origin.strip()
        ]

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


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_provider_key()
    return settings
