from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

APP_VERSION = "0.1.0"


class Settings(BaseSettings):
    """Env-driven application configuration.

    Values are read from environment variables (or a local `.env` file in
    development). See `backend/python/.env.example` for the full list.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_provider: str = "openai"

    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"

    gemini_api_key: str | None = None
    gemini_model: str = "gemini-3.1-flash-lite"

    cors_allowed_origins: str = "http://localhost:5173"

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
        if self.llm_provider == "openai" and not self.openai_api_key:
            raise ValueError(
                "LLM_PROVIDER is 'openai' but OPENAI_API_KEY is not set. "
                "Set it in backend/python/.env (see .env.example)."
            )
        if self.llm_provider == "gemini" and not self.gemini_api_key:
            raise ValueError(
                "LLM_PROVIDER is 'gemini' but GEMINI_API_KEY is not set. "
                "Set it in backend/python/.env (see .env.example)."
            )


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_provider_key()
    return settings
