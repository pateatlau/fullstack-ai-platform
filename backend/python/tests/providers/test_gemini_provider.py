import asyncio
import sys
from pathlib import Path
from collections.abc import Iterator
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core.config import Settings, get_settings
from app.providers.base import ProviderChunk
from app.providers.factory import ProviderFactory
from app.providers.gemini_provider import GeminiProvider
from app.providers.openai_provider import OpenAIProvider
from app.schemas.chat import ChatMessageSchema


class _FakeChunk:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeModels:
    def generate_content(
        self, model: str, contents: str, config: dict[str, Any]
    ) -> _FakeResponse:
        assert model
        assert contents
        assert "temperature" in config
        return _FakeResponse("Gemini full response")

    def generate_content_stream(
        self, model: str, contents: str, config: dict[str, Any]
    ) -> Iterator[_FakeChunk]:
        assert model
        assert contents
        assert "temperature" in config
        yield _FakeChunk("Gemini ")
        yield _FakeChunk("stream")


class _FakeClient:
    def __init__(self) -> None:
        self.models = _FakeModels()


def test_complete_chat_returns_text() -> None:
    provider = GeminiProvider(api_key="test-key")
    provider._client = _FakeClient()  # type: ignore[assignment]

    result = asyncio.run(
        provider.complete_chat(
            messages=[ChatMessageSchema(role="user", content="hello")],
            model="gemini-3.1-flash-lite",
            temperature=0.7,
        )
    )

    assert result == "Gemini full response"


def test_stream_chat_yields_incremental_chunks() -> None:
    provider = GeminiProvider(api_key="test-key")
    provider._client = _FakeClient()  # type: ignore[assignment]

    async def gather_chunks() -> list[ProviderChunk]:
        chunks: list[ProviderChunk] = []
        async for chunk in provider.stream_chat(
            messages=[ChatMessageSchema(role="user", content="hello")],
            model="gemini-3.1-flash-lite",
            temperature=0.7,
        ):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(gather_chunks())

    assert chunks == [
        {"content": "Gemini ", "finish_reason": None},
        {"content": "stream", "finish_reason": None},
    ]


def test_factory_returns_gemini_when_settings_select_it() -> None:
    provider = ProviderFactory.get_provider(
        settings=Settings(
            llm_provider="gemini",
            gemini_api_key="test-key",
            openai_api_key=None,
        )
    )

    assert isinstance(provider, GeminiProvider)


def test_factory_keeps_openai_path_compatible() -> None:
    provider = ProviderFactory.get_provider(
        settings=Settings(
            llm_provider="openai",
            openai_api_key="test-key",
            gemini_api_key=None,
        )
    )

    assert isinstance(provider, OpenAIProvider)


def test_get_settings_reads_gemini_provider_from_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    get_settings.cache_clear()

    try:
        settings = get_settings()
    finally:
        get_settings.cache_clear()

    assert settings.llm_provider == "gemini"
    assert settings.gemini_model == "gemini-3.1-flash-lite"


def test_get_settings_fails_fast_when_selected_provider_key_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    get_settings.cache_clear()

    try:
        with pytest.raises(ValueError, match="GEMINI_API_KEY"):
            get_settings()
    finally:
        get_settings.cache_clear()
