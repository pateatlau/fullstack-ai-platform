"""AI settings defaults and feature-flag startup validation."""

from __future__ import annotations

import pytest
from pydantic_settings import SettingsConfigDict

from app.ai import deps as ai_deps
from app.core.config import Settings


def test_ai_settings_load_with_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        Settings,
        "model_config",
        SettingsConfigDict(env_file=None, extra="ignore"),
    )
    settings = Settings(
        llm_provider="openai",
        openai_api_key="sk-placeholder",
    )
    assert settings.embedding_provider == "openai"
    assert settings.embedding_model == "text-embedding-3-small"
    assert settings.embedding_dimensions == 1536
    assert settings.chunk_size == 1000
    assert settings.chunk_overlap == 200
    assert settings.rag_top_k == 5
    assert settings.rag_default_prompt_template == "rag/answer/v1"
    assert settings.rag_context_max_chars == 8000
    assert settings.default_temperature == 0.7
    assert settings.default_max_tokens is None
    assert settings.document_upload_max_bytes == 10_485_760
    assert settings.web_search_provider == "tavily"
    assert settings.web_search_api_key is None
    assert settings.web_search_max_results == 5
    assert settings.guest_max_output_tokens == 4096
    assert settings.authenticated_daily_upload_quota is None
    assert settings.guest_daily_upload_quota == 5
    assert settings.demo_mode_strict is False


def test_feature_flags_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        Settings,
        "model_config",
        SettingsConfigDict(env_file=None, extra="ignore"),
    )
    settings = Settings(
        llm_provider="openai",
        openai_api_key="sk-placeholder",
    )
    assert settings.rag_enabled is False
    assert settings.tools_enabled is False
    assert settings.chat_streaming_enabled is True


def test_rag_enabled_requires_embedding_provider_key() -> None:
    settings = Settings(
        llm_provider="gemini",
        gemini_api_key="gm-placeholder",
        openai_api_key=None,
        rag_enabled=True,
    )
    with pytest.raises(
        ValueError, match="RAG_ENABLED is true but OPENAI_API_KEY is not set"
    ):
        settings.validate_startup()


def test_rag_enabled_rejects_unsupported_embedding_provider() -> None:
    settings = Settings(
        llm_provider="openai",
        openai_api_key="sk-placeholder",
        rag_enabled=True,
        embedding_provider="gemini",
    )
    with pytest.raises(ValueError, match="Unsupported EMBEDDING_PROVIDER"):
        settings.validate_startup()


def test_rag_enabled_rejects_chunk_overlap_greater_than_or_equal_to_chunk_size() -> (
    None
):
    settings = Settings(
        llm_provider="openai",
        openai_api_key="sk-placeholder",
        rag_enabled=True,
        chunk_size=1000,
        chunk_overlap=1000,
    )
    with pytest.raises(ValueError, match="CHUNK_OVERLAP must be less than CHUNK_SIZE"):
        settings.validate_startup()


def test_rag_disabled_skips_chunk_boundary_validation() -> None:
    settings = Settings(
        llm_provider="openai",
        openai_api_key="sk-placeholder",
        rag_enabled=False,
        chunk_size=1000,
        chunk_overlap=1000,
    )
    settings.validate_startup()


def test_tools_enabled_requires_web_search_api_key() -> None:
    settings = Settings(
        llm_provider="openai",
        openai_api_key="sk-placeholder",
        tools_enabled=True,
        web_search_api_key=None,
    )
    with pytest.raises(
        ValueError, match="TOOLS_ENABLED is true but WEB_SEARCH_API_KEY is not set"
    ):
        settings.validate_startup()


def test_production_rag_enabled_requires_embedding_key() -> None:
    settings = Settings(
        app_env="production",
        llm_provider="gemini",
        gemini_api_key="gm-live",
        openai_api_key=None,
        jwt_secret="production-jwt-secret-with-enough-length",
        database_url="postgresql+asyncpg://prod:prod@db.example.com:5432/chatbot",
        google_client_id="1234567890.apps.googleusercontent.com",
        rag_enabled=True,
    )
    with pytest.raises(
        ValueError, match="RAG_ENABLED is true but OPENAI_API_KEY is not set"
    ):
        settings.validate_startup()


def test_production_tools_enabled_requires_web_search_key() -> None:
    settings = Settings(
        app_env="production",
        llm_provider="openai",
        openai_api_key="sk-live",
        jwt_secret="production-jwt-secret-with-enough-length",
        database_url="postgresql+asyncpg://prod:prod@db.example.com:5432/chatbot",
        google_client_id="1234567890.apps.googleusercontent.com",
        tools_enabled=True,
        web_search_api_key=None,
    )
    with pytest.raises(
        ValueError, match="TOOLS_ENABLED is true but WEB_SEARCH_API_KEY is not set"
    ):
        settings.validate_startup()


def test_flags_off_startup_validation_unchanged() -> None:
    settings = Settings(
        llm_provider="openai",
        openai_api_key="sk-placeholder",
        rag_enabled=False,
        tools_enabled=False,
    )
    settings.validate_startup()


def test_rag_and_tools_enabled_with_required_secrets_passes() -> None:
    settings = Settings(
        llm_provider="openai",
        openai_api_key="sk-placeholder",
        rag_enabled=True,
        tools_enabled=True,
        web_search_api_key="tvly-placeholder",
    )
    settings.validate_startup()


def test_default_max_tokens_empty_env_value_means_provider_default() -> None:
    settings = Settings.model_validate(
        {
            "llm_provider": "openai",
            "openai_api_key": "sk-placeholder",
            "default_max_tokens": "",
        }
    )
    assert settings.default_max_tokens is None


def test_authenticated_daily_upload_quota_empty_env_means_unlimited() -> None:
    settings = Settings.model_validate(
        {
            "llm_provider": "openai",
            "openai_api_key": "sk-placeholder",
            "authenticated_daily_upload_quota": "",
        }
    )
    assert settings.authenticated_daily_upload_quota is None


@pytest.mark.parametrize("invalid_quota", [0, -1])
def test_authenticated_daily_upload_quota_rejects_non_positive(
    invalid_quota: int,
) -> None:
    with pytest.raises(ValueError, match="greater than or equal to 1"):
        Settings.model_validate(
            {
                "llm_provider": "openai",
                "openai_api_key": "sk-placeholder",
                "authenticated_daily_upload_quota": invalid_quota,
            }
        )


def test_authenticated_daily_upload_quota_accepts_positive_value() -> None:
    settings = Settings.model_validate(
        {
            "llm_provider": "openai",
            "openai_api_key": "sk-placeholder",
            "authenticated_daily_upload_quota": 20,
        }
    )
    assert settings.authenticated_daily_upload_quota == 20


def test_ai_package_imports_cleanly() -> None:
    assert ai_deps.get_ai_settings is not None
