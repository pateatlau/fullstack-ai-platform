"""Regression: unified chat passes tool-loop dict history into stream_chat."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.providers.anthropic_provider import AnthropicProvider
from app.providers.base import ProviderChunk
from app.providers.gemini_provider import GeminiProvider
from app.providers.groq_provider import GroqProvider
from app.providers.openai_provider import OpenAIProvider
from app.schemas.chat import ChatMessageSchema

TOOL_LOOP_HISTORY: list[Any] = [
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
]


async def _collect_stream(
    provider: Any,
    *,
    model: str,
) -> list[ProviderChunk]:
    chunks: list[ProviderChunk] = []
    async for chunk in provider.stream_chat(
        cast(list[ChatMessageSchema], TOOL_LOOP_HISTORY),
        model,
        temperature=0.7,
    ):
        chunks.append(chunk)
    return chunks


def test_openai_stream_chat_accepts_tool_loop_dict_messages() -> None:
    captured: dict[str, Any] = {}

    async def _fake_create(**kwargs: Any) -> AsyncIterator[Any]:
        captured.update(kwargs)

        class _Event:
            choices = [
                type(
                    "Choice",
                    (),
                    {
                        "delta": type("Delta", (), {"content": "Grounded answer"})(),
                        "finish_reason": None,
                    },
                )()
            ]

        async def _iterate() -> AsyncIterator[Any]:
            yield _Event()

        return _iterate()

    provider = OpenAIProvider(api_key="test-key")
    provider._client = MagicMock()
    provider._client.chat.completions.create = AsyncMock(side_effect=_fake_create)

    chunks = asyncio.run(_collect_stream(provider, model="gpt-4o-mini"))

    assert chunks == [{"content": "Grounded answer", "finish_reason": None}]
    messages = captured["messages"]
    assert messages[0] == {"role": "user", "content": "Search for news"}
    assert messages[1]["role"] == "assistant"
    assert messages[1]["tool_calls"][0]["function"]["name"] == "web_search"
    assert messages[2] == {
        "role": "tool",
        "tool_call_id": "call-1",
        "content": '{"success": true, "results": []}',
    }


def test_groq_stream_chat_accepts_tool_loop_dict_messages() -> None:
    captured: dict[str, Any] = {}

    class _FakeStreamChoice:
        delta = type("Delta", (), {"content": "Grounded answer"})()
        finish_reason = None

    class _FakeStreamEvent:
        choices = [_FakeStreamChoice()]

    class _FakeCompletions:
        async def create(self, **kwargs: Any) -> AsyncIterator[_FakeStreamEvent]:
            captured.update(kwargs)

            async def _iterate() -> AsyncIterator[_FakeStreamEvent]:
                yield _FakeStreamEvent()

            return _iterate()

    provider = GroqProvider(api_key="test-key")
    cast(Any, provider)._client = type(
        "Client", (), {"chat": type("Chat", (), {"completions": _FakeCompletions()})()}
    )()

    chunks = asyncio.run(_collect_stream(provider, model="openai/gpt-oss-20b"))

    assert chunks == [{"content": "Grounded answer", "finish_reason": None}]
    messages = captured["messages"]
    assert messages[1]["tool_calls"][0]["function"]["name"] == "web_search"
    assert messages[2]["role"] == "tool"


def test_anthropic_stream_chat_accepts_tool_loop_dict_messages() -> None:
    captured: dict[str, Any] = {}

    class _FakeTextDelta:
        type = "text_delta"
        text = "Grounded answer"

    class _FakeContentBlockDeltaEvent:
        type = "content_block_delta"
        delta = _FakeTextDelta()

    class _FakeStreamIterator:
        def __aiter__(self) -> "_FakeStreamIterator":
            return self

        async def __anext__(self) -> _FakeContentBlockDeltaEvent:
            if getattr(self, "_done", False):
                raise StopAsyncIteration
            self._done = True
            return _FakeContentBlockDeltaEvent()

    class _FakeStreamManager:
        async def __aenter__(self) -> _FakeStreamIterator:
            return _FakeStreamIterator()

        async def __aexit__(self, *_args: Any) -> None:
            return None

    class _FakeMessagesApi:
        def stream(self, **kwargs: Any) -> _FakeStreamManager:
            captured.update(kwargs)
            return _FakeStreamManager()

    provider = AnthropicProvider(api_key="test-key")
    provider._client = type("Client", (), {"messages": _FakeMessagesApi()})()  # type: ignore[assignment]

    chunks = asyncio.run(_collect_stream(provider, model="claude-haiku-4-5-20251001"))

    assert chunks == [{"content": "Grounded answer", "finish_reason": None}]
    messages = captured["messages"]
    assistant_blocks = messages[1]["content"]
    assert assistant_blocks[0]["type"] == "tool_use"
    assert assistant_blocks[0]["name"] == "web_search"
    assert messages[2]["content"][0]["type"] == "tool_result"


def test_gemini_stream_chat_accepts_tool_loop_dict_messages() -> None:
    captured: dict[str, Any] = {}

    class _FakeChunk:
        text = "Grounded answer"

    class _CapturingModels:
        def generate_content_stream(
            self, model: str, contents: list[Any], config: dict[str, Any]
        ) -> Any:
            captured["model"] = model
            captured["contents"] = contents
            captured["config"] = config
            yield _FakeChunk()

    provider = GeminiProvider(api_key="test-key")
    provider._client = type("Client", (), {"models": _CapturingModels()})()  # type: ignore[assignment]

    chunks = asyncio.run(_collect_stream(provider, model="gemini-3.1-flash-lite"))

    assert chunks == [{"content": "Grounded answer", "finish_reason": None}]
    function_response = captured["contents"][-1].parts[0].function_response
    assert function_response.name == "web_search"
