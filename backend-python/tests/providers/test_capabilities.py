"""Provider capability lookup tests."""

from __future__ import annotations

from app.providers.capabilities import (
    ALL_PROVIDERS,
    capabilities_by_provider,
    get_capabilities,
)


def test_get_capabilities_returns_streaming_and_tool_calling_flags() -> None:
    for provider in ALL_PROVIDERS:
        caps = get_capabilities(provider)
        assert caps.supports_streaming is True
        assert caps.supports_tool_calling is True
        assert caps.supports_json_mode is False
        assert caps.supports_embeddings is False


def test_capabilities_by_provider_serializes_all_providers() -> None:
    payload = capabilities_by_provider()
    assert set(payload.keys()) == set(ALL_PROVIDERS)
    for provider in ALL_PROVIDERS:
        entry = payload[provider]
        assert entry["supports_streaming"] is True
        assert entry["supports_tool_calling"] is True
        assert "supports_json_mode" in entry
        assert "supports_reasoning" in entry
