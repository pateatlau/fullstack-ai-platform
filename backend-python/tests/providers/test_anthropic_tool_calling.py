"""Anthropic provider tool-calling adapter tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.providers.anthropic_provider import AnthropicProvider
from app.providers.base import ProviderToolCall
from app.schemas.chat import ChatMessageSchema

pytestmark = pytest.mark.anyio


class _FakeToolUseBlock:
    def __init__(self) -> None:
        self.type = "tool_use"
        self.id = "toolu_abc"
        self.name = "web_search"
        self.input = {"query": "weather"}


class _FakeTextBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


def _tool_use_response() -> MagicMock:
    response = MagicMock()
    response.content = [_FakeToolUseBlock()]
    response.stop_reason = "tool_use"
    response.usage = None
    return response


async def test_anthropic_complete_chat_with_tools_parses_tool_calls() -> None:
    provider = AnthropicProvider(api_key="test-key")
    provider._client = MagicMock()
    provider._client.messages.create = AsyncMock(return_value=_tool_use_response())

    completion = await provider.complete_chat_with_tools(
        [ChatMessageSchema(role="user", content="What's the weather?")],
        model="claude-sonnet-4-20250514",
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "Search the web",
                    "parameters": {"type": "object"},
                },
            }
        ],
    )

    assert completion.tool_calls == [
        ProviderToolCall(
            id="toolu_abc", name="web_search", arguments={"query": "weather"}
        )
    ]
    assert completion.content is None
    assert completion.finish_reason == "tool_use"


async def test_anthropic_complete_chat_with_tools_handles_direct_answer() -> None:
    provider = AnthropicProvider(api_key="test-key")
    response = MagicMock()
    response.content = [_FakeTextBlock("Direct answer")]
    response.stop_reason = "end_turn"
    response.usage = None

    provider._client = MagicMock()
    provider._client.messages.create = AsyncMock(return_value=response)

    completion = await provider.complete_chat_with_tools(
        [ChatMessageSchema(role="user", content="Hello")],
        model="claude-sonnet-4-20250514",
        tools=[],
    )

    assert completion.tool_calls == []
    assert completion.content == "Direct answer"


async def test_anthropic_complete_chat_with_tools_handles_malformed_arguments() -> None:
    provider = AnthropicProvider(api_key="test-key")

    class _BadInputBlock:
        type = "tool_use"
        id = "toolu_bad"
        name = "web_search"
        input: Any = "not-a-dict"

    response = MagicMock()
    response.content = [_BadInputBlock()]
    response.stop_reason = "tool_use"
    response.usage = None

    provider._client = MagicMock()
    provider._client.messages.create = AsyncMock(return_value=response)

    completion = await provider.complete_chat_with_tools(
        [ChatMessageSchema(role="user", content="Search")],
        model="claude-sonnet-4-20250514",
        tools=[],
    )

    assert completion.tool_calls == [
        ProviderToolCall(id="toolu_bad", name="web_search", arguments={})
    ]
