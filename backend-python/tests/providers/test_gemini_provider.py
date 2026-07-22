import asyncio
import sys
from pathlib import Path
from collections.abc import Iterator
from typing import Any, cast

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.core.config import Settings, get_settings
from app.providers.anthropic_provider import AnthropicProvider
from app.providers.base import ProviderChunk
from app.providers.factory import ProviderFactory
from app.providers.gemini_provider import GeminiProvider
from app.providers.groq_provider import GroqProvider
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
        self, model: str, contents: str | list[Any], config: dict[str, Any]
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

    assert result.content == "Gemini full response"


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


def test_stream_chat_yields_terminal_finish_reason_without_text() -> None:
    class _FinishOnlyChunk:
        def __init__(self) -> None:
            self.candidates = [_FakeFinishCandidate()]

    class _FakeFinishCandidate:
        def __init__(self) -> None:
            self.content = type("Content", (), {"parts": []})()
            self.finish_reason = "STOP"

    class _FinishOnlyModels:
        def generate_content_stream(
            self, model: str, contents: str, config: dict[str, Any]
        ) -> Iterator[_FinishOnlyChunk]:
            del model, contents, config
            yield _FinishOnlyChunk()

    provider = GeminiProvider(api_key="test-key")
    provider._client = type("Client", (), {"models": _FinishOnlyModels()})()  # type: ignore[assignment]

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

    assert chunks == [{"content": "", "finish_reason": "STOP"}]


def test_stream_chat_accepts_tool_loop_dict_messages() -> None:
    captured: dict[str, Any] = {}

    class _CapturingModels:
        def generate_content_stream(
            self, model: str, contents: list[Any], config: dict[str, Any]
        ) -> Iterator[_FakeChunk]:
            captured["model"] = model
            captured["contents"] = contents
            captured["config"] = config
            yield _FakeChunk("Grounded answer")

    provider = GeminiProvider(api_key="test-key")
    provider._client = type("Client", (), {"models": _CapturingModels()})()  # type: ignore[assignment]

    async def gather_chunks() -> list[ProviderChunk]:
        chunks: list[ProviderChunk] = []
        async for chunk in provider.stream_chat(
            cast(
                list[ChatMessageSchema],
                [
                    ChatMessageSchema(role="user", content="Search for news"),
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {
                                    "name": "web_search",
                                    "arguments": '{"query": "news"}',
                                },
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": "call-1",
                        "content": '{"success": true, "results": []}',
                    },
                ],
            ),
            model="gemini-3.1-flash-lite",
            temperature=0.7,
        ):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(gather_chunks())

    assert chunks == [{"content": "Grounded answer", "finish_reason": None}]
    assert len(captured["contents"]) == 3
    function_response = captured["contents"][-1].parts[0].function_response
    assert function_response.name == "web_search"


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


def test_factory_returns_groq_when_settings_select_it() -> None:
    provider = ProviderFactory.get_provider(
        settings=Settings(
            llm_provider="groq",
            groq_api_key="test-key",
            openai_api_key=None,
            gemini_api_key=None,
        )
    )

    assert isinstance(provider, GroqProvider)


def test_factory_returns_anthropic_when_settings_select_it() -> None:
    provider = ProviderFactory.get_provider(
        settings=Settings(
            llm_provider="anthropic",
            anthropic_api_key="test-key",
            openai_api_key=None,
            gemini_api_key=None,
            groq_api_key=None,
        )
    )

    assert isinstance(provider, AnthropicProvider)


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


def test_get_settings_reads_groq_provider_from_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    get_settings.cache_clear()

    try:
        settings = get_settings()
    finally:
        get_settings.cache_clear()

    assert settings.llm_provider == "groq"
    assert settings.groq_model == "openai/gpt-oss-20b"


def test_get_settings_reads_anthropic_provider_from_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    get_settings.cache_clear()

    try:
        settings = get_settings()
    finally:
        get_settings.cache_clear()

    assert settings.llm_provider == "anthropic"
    assert settings.anthropic_model == "claude-haiku-4-5-20251001"


def test_get_settings_fails_fast_when_groq_key_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    get_settings.cache_clear()

    try:
        with pytest.raises(ValueError, match="GROQ_API_KEY"):
            get_settings()
    finally:
        get_settings.cache_clear()


def test_get_settings_fails_fast_when_anthropic_key_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    get_settings.cache_clear()

    try:
        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
            get_settings()
    finally:
        get_settings.cache_clear()


def test_get_settings_fails_fast_for_unsupported_provider_value(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_PROVIDER", "invalid-provider")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    get_settings.cache_clear()

    try:
        with pytest.raises(ValueError, match="Unsupported LLM_PROVIDER"):
            get_settings()
    finally:
        get_settings.cache_clear()
