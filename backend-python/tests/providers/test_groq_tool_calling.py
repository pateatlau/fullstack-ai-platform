"""Groq provider tool-calling adapter tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.providers.base import ProviderToolCall
from app.providers.groq_provider import GroqProvider
from app.schemas.chat import ChatMessageSchema

pytestmark = pytest.mark.anyio


def _tool_call_message() -> MagicMock:
    function = MagicMock()
    function.name = "web_search"
    function.arguments = '{"query": "weather"}'

    tool_call = MagicMock()
    tool_call.type = "function"
    tool_call.id = "call_groq"
    tool_call.function = function

    message = MagicMock()
    message.content = None
    message.tool_calls = [tool_call]

    choice = MagicMock()
    choice.message = message
    choice.finish_reason = "tool_calls"

    response = MagicMock()
    response.choices = [choice]
    response.usage = None
    return response


async def test_groq_complete_chat_with_tools_parses_tool_calls() -> None:
    provider = GroqProvider(api_key="test-key")
    mock_create = AsyncMock(return_value=_tool_call_message())
    provider._client = MagicMock()
    provider._client.chat.completions.create = mock_create

    completion = await provider.complete_chat_with_tools(
        [ChatMessageSchema(role="user", content="What's the weather?")],
        model="llama-3.3-70b-versatile",
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
            id="call_groq", name="web_search", arguments={"query": "weather"}
        )
    ]
    assert completion.content is None
    assert completion.finish_reason == "tool_calls"


async def test_groq_complete_chat_with_tools_handles_direct_answer() -> None:
    provider = GroqProvider(api_key="test-key")

    message = MagicMock()
    message.content = "Direct answer"
    message.tool_calls = []

    choice = MagicMock()
    choice.message = message
    choice.finish_reason = "stop"

    response = MagicMock()
    response.choices = [choice]
    response.usage = None

    provider._client = MagicMock()
    provider._client.chat.completions.create = AsyncMock(return_value=response)

    completion = await provider.complete_chat_with_tools(
        [ChatMessageSchema(role="user", content="Hello")],
        model="llama-3.3-70b-versatile",
        tools=[],
    )

    assert completion.tool_calls == []
    assert completion.content == "Direct answer"


async def test_groq_complete_chat_with_tools_handles_malformed_arguments() -> None:
    provider = GroqProvider(api_key="test-key")

    function = MagicMock()
    function.name = "web_search"
    function.arguments = "not-json"

    tool_call = MagicMock()
    tool_call.type = "function"
    tool_call.id = "call_bad"
    tool_call.function = function

    message = MagicMock()
    message.content = None
    message.tool_calls = [tool_call]

    choice = MagicMock()
    choice.message = message
    choice.finish_reason = "tool_calls"

    response = MagicMock()
    response.choices = [choice]
    response.usage = None

    provider._client = MagicMock()
    provider._client.chat.completions.create = AsyncMock(return_value=response)

    completion = await provider.complete_chat_with_tools(
        [ChatMessageSchema(role="user", content="Search")],
        model="llama-3.3-70b-versatile",
        tools=[],
    )

    assert completion.tool_calls == [
        ProviderToolCall(id="call_bad", name="web_search", arguments={})
    ]
