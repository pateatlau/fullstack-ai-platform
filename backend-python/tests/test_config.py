"""Settings validation and environment-aware startup behavior."""

from __future__ import annotations

import logging

import pytest

from app.core.config import (
    Settings,
    _DEFAULT_DATABASE_URL,
    _LEGACY_DEFAULT_DATABASE_URL,
    get_settings,
)
from app.core.logging import get_logger


@pytest.fixture(autouse=True)
def _clear_settings_cache():  # pyright: ignore[reportUnusedFunction]
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_development_settings_load_with_example_placeholders() -> None:
    settings = Settings(
        llm_provider="openai",
        openai_api_key="sk-placeholder",
        app_env="development",
        rate_limit_anonymous_per_minute=30,
        rate_limit_authenticated_per_minute=120,
    )
    settings.validate_startup()
    assert settings.is_development
    assert settings.request_body_limit_bytes == 16 * 1024
    assert settings.log_level == "INFO"
    assert settings.rate_limit_anonymous_per_minute == 30
    assert settings.rate_limit_authenticated_per_minute == 120


def test_missing_provider_key_raises() -> None:
    settings = Settings(llm_provider="openai", openai_api_key=None)
    with pytest.raises(ValueError, match="OPENAI_API_KEY is not set"):
        settings.validate_startup()


def test_production_rejects_default_jwt_secret() -> None:
    settings = Settings(
        app_env="production",
        llm_provider="openai",
        openai_api_key="sk-live",
        jwt_secret="dev-insecure-jwt-secret-change-me",
        database_url="postgresql+asyncpg://prod:prod@db.example.com:5432/chatbot",
        google_client_id="1234567890.apps.googleusercontent.com",
    )
    with pytest.raises(ValueError, match="JWT_SECRET must be explicitly set"):
        settings.validate_startup()


@pytest.mark.parametrize(
    "database_url",
    [_DEFAULT_DATABASE_URL, _LEGACY_DEFAULT_DATABASE_URL],
    ids=["5433", "5432"],
)
def test_production_rejects_default_database_url(database_url: str) -> None:
    settings = Settings(
        app_env="production",
        llm_provider="openai",
        openai_api_key="sk-live",
        jwt_secret="production-jwt-secret-with-enough-length",
        google_client_id="1234567890.apps.googleusercontent.com",
        database_url=database_url,
    )
    with pytest.raises(ValueError, match="DATABASE_URL must be explicitly set"):
        settings.validate_startup()


def test_production_requires_google_client_id() -> None:
    settings = Settings(
        app_env="production",
        llm_provider="openai",
        openai_api_key="sk-live",
        jwt_secret="production-jwt-secret-with-enough-length",
        database_url="postgresql+asyncpg://prod:prod@db.example.com:5432/chatbot",
        google_client_id="",
    )
    with pytest.raises(ValueError, match="GOOGLE_CLIENT_ID must be set"):
        settings.validate_startup()


def test_production_accepts_valid_configuration() -> None:
    settings = Settings(
        app_env="production",
        llm_provider="openai",
        openai_api_key="sk-live",
        jwt_secret="production-jwt-secret-with-enough-length",
        database_url="postgresql+asyncpg://prod:prod@db.example.com:5432/chatbot",
        google_client_id="1234567890.apps.googleusercontent.com",
    )
    settings.validate_startup()


def test_invalid_log_level_rejected() -> None:
    with pytest.raises(ValueError, match="LOG_LEVEL must be one of"):
        Settings.model_validate({"log_level": "VERBOSE"})


def test_log_level_is_normalized_to_uppercase() -> None:
    settings = Settings.model_validate({"log_level": "debug"})
    assert settings.log_level == "DEBUG"


def test_request_body_limit_message_uses_configured_limit() -> None:
    settings = Settings(request_body_limit_bytes=8192)
    assert "8192 byte limit" in settings.request_body_limit_message()


@pytest.mark.parametrize(
    "database_url",
    [_DEFAULT_DATABASE_URL, _LEGACY_DEFAULT_DATABASE_URL],
    ids=["5433", "5432"],
)
def test_development_warnings_for_insecure_defaults(
    caplog: pytest.LogCaptureFixture,
    database_url: str,
) -> None:
    settings = Settings(
        app_env="development",
        llm_provider="openai",
        openai_api_key="sk-placeholder",
        jwt_secret="dev-insecure-jwt-secret-change-me",
        google_client_id="",
        database_url=database_url,
    )
    logger = get_logger("test.config")
    with caplog.at_level(logging.WARNING, logger="test.config"):
        settings.log_development_warnings(logger)

    messages = " ".join(record.message for record in caplog.records)
    assert "JWT_SECRET" in messages
    assert "GOOGLE_CLIENT_ID" in messages
    assert "DATABASE_URL" in messages


def test_get_settings_caches_result() -> None:
    first = get_settings()
    second = get_settings()
    assert first is second


def test_voice_disabled_skips_validation() -> None:
    """Voice validation should be skipped when VOICE_ENABLED=false."""
    settings = Settings(
        llm_provider="openai",
        openai_api_key="sk-placeholder",
        voice_enabled=False,
        voice_provider="invalid_provider",
        voice_audio_encoding="invalid_encoding",
    )
    settings.validate_startup()  # Should not raise


def test_voice_enabled_with_unsupported_provider_raises() -> None:
    """Voice validation should reject unsupported providers."""
    settings = Settings(
        llm_provider="openai",
        openai_api_key="sk-placeholder",
        voice_enabled=True,
        voice_provider="deepgram",
    )
    with pytest.raises(ValueError, match="Unsupported VOICE_PROVIDER"):
        settings.validate_startup()


def test_voice_enabled_with_unsupported_encoding_raises() -> None:
    """Voice validation should reject unsupported audio encodings."""
    settings = Settings(
        llm_provider="openai",
        openai_api_key="sk-placeholder",
        voice_enabled=True,
        voice_provider="openai",
        voice_audio_encoding="opus",
    )
    with pytest.raises(ValueError, match="Unsupported VOICE_AUDIO_ENCODING"):
        settings.validate_startup()


def test_voice_enabled_openai_without_api_key_raises() -> None:
    """Voice validation should require OpenAI API key when provider is openai."""
    settings = Settings(
        llm_provider="gemini",
        gemini_api_key="gm-placeholder",
        voice_enabled=True,
        voice_provider="openai",
        openai_api_key=None,
    )
    with pytest.raises(ValueError, match="OPENAI_API_KEY is not set"):
        settings.validate_startup()


def test_voice_enabled_with_invalid_timeout_relationship_raises() -> None:
    """Voice validation should ensure heartbeat interval < session timeout."""
    settings = Settings(
        llm_provider="openai",
        openai_api_key="sk-placeholder",
        voice_enabled=True,
        voice_provider="openai",
        voice_heartbeat_interval_seconds=300,
        voice_session_timeout_seconds=300,
    )
    with pytest.raises(
        ValueError,
        match="VOICE_HEARTBEAT_INTERVAL_SECONDS must be less than "
        "VOICE_SESSION_TIMEOUT_SECONDS",
    ):
        settings.validate_startup()


def test_voice_enabled_with_valid_configuration() -> None:
    """Voice validation should pass with valid configuration."""
    settings = Settings(
        llm_provider="openai",
        openai_api_key="sk-placeholder",
        voice_enabled=True,
        voice_provider="openai",
        voice_audio_encoding="pcm16",
        voice_heartbeat_interval_seconds=30,
        voice_session_timeout_seconds=300,
    )
    settings.validate_startup()  # Should not raise


def test_memory_disabled_skips_validation() -> None:
    """Memory validation should be skipped when MEMORY_ENABLED=false."""
    settings = Settings(
        llm_provider="openai",
        openai_api_key="sk-placeholder",
        memory_enabled=False,
        memory_provider="invalid_provider",
    )
    settings.validate_startup()  # Should not raise


def test_memory_enabled_with_unsupported_provider_raises() -> None:
    """Memory validation should reject unsupported providers."""
    settings = Settings(
        llm_provider="openai",
        openai_api_key="sk-placeholder",
        memory_enabled=True,
        memory_provider="pinecone",
    )
    with pytest.raises(ValueError, match="Unsupported MEMORY_PROVIDER"):
        settings.validate_startup()


def test_memory_enabled_with_unsupported_embedding_provider_raises() -> None:
    """Memory validation should reject unsupported embedding providers."""
    settings = Settings(
        llm_provider="openai",
        openai_api_key="sk-placeholder",
        memory_enabled=True,
        embedding_provider="gemini",
    )
    with pytest.raises(ValueError, match="Unsupported EMBEDDING_PROVIDER"):
        settings.validate_startup()


def test_memory_enabled_openai_embeddings_without_api_key_raises() -> None:
    """Memory validation should require OpenAI API key for OpenAI embeddings."""
    settings = Settings(
        llm_provider="gemini",
        gemini_api_key="gm-placeholder",
        memory_enabled=True,
        embedding_provider="openai",
        openai_api_key=None,
    )
    with pytest.raises(ValueError, match="OPENAI_API_KEY is not set"):
        settings.validate_startup()


def test_memory_enabled_with_valid_configuration() -> None:
    """Memory validation should pass with valid configuration."""
    settings = Settings(
        llm_provider="openai",
        openai_api_key="sk-placeholder",
        memory_enabled=True,
        memory_provider="pgvector",
    )
    settings.validate_startup()  # Should not raise


def test_memory_configuration_defaults() -> None:
    """Memory settings default to the frozen Part I configuration defaults."""
    fields = Settings.model_fields

    assert fields["memory_enabled"].default is False
    assert fields["memory_provider"].default == "pgvector"
    assert fields["memory_retrieval_top_k"].default == 8
    assert fields["memory_min_quality_score"].default == 0.4
    assert fields["memory_min_confidence"].default == 0.5
    assert fields["memory_dedupe_similarity_threshold"].default == 0.92
    assert fields["memory_token_budget"].default == 1500
    assert fields["memory_extraction_enabled"].default is True
    assert fields["memory_extraction_model"].default == ""
    assert fields["memory_archived_retention_days"].default == 90
