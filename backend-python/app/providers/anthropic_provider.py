import json
import uuid
from typing import Any, AsyncIterator, cast

from anthropic import AsyncAnthropic
from anthropic.types import Message, MessageParam

from app.providers.base import (
    ChatMessageInput,
    ProviderChunk,
    ProviderCompletion,
    ProviderToolCall,
    ProviderToolCompletion,
    ProviderUsage,
)
from app.schemas.chat import ChatMessageSchema

ANTHROPIC_MAX_TOKENS = 1024


def _usage_from_message(response: Message) -> ProviderUsage | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    input_tokens = getattr(usage, "input_tokens", None)
    output_tokens = getattr(usage, "output_tokens", None)
    total = (
        input_tokens + output_tokens
        if input_tokens is not None and output_tokens is not None
        else None
    )
    return ProviderUsage(
        prompt_tokens=input_tokens,
        completion_tokens=output_tokens,
        total_tokens=total,
    )


def _split_messages_for_anthropic(
    messages: list[ChatMessageSchema],
) -> tuple[str | None, list[MessageParam]]:
    return _split_tool_messages_for_anthropic(cast(list[ChatMessageInput], messages))


def _parse_tool_arguments(raw: str | dict[str, object] | None) -> dict[str, object]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return cast(dict[str, object], raw)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if isinstance(parsed, dict):
        return cast(dict[str, object], parsed)
    return {}


def _split_tool_messages_for_anthropic(
    messages: list[ChatMessageInput],
) -> tuple[str | None, list[MessageParam]]:
    system_parts: list[str] = []
    chat_messages: list[MessageParam] = []

    for message in messages:
        if isinstance(message, ChatMessageSchema):
            if message.role == "system":
                system_parts.append(message.content)
                continue
            chat_messages.append(
                MessageParam(role=message.role, content=message.content)
            )
            continue

        role = message.get("role")
        if role == "assistant" and message.get("tool_calls"):
            content_blocks: list[dict[str, object]] = []
            text_content = message.get("content")
            if isinstance(text_content, str) and text_content:
                content_blocks.append({"type": "text", "text": text_content})
            for call in message.get("tool_calls", []):
                if not isinstance(call, dict) or call.get("type") != "function":
                    continue
                function = call.get("function")
                if not isinstance(function, dict):
                    continue
                raw_args = function.get("arguments")
                args = _parse_tool_arguments(
                    raw_args if isinstance(raw_args, (str, dict)) else None
                )
                content_blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.get("id") or f"tool_{uuid.uuid4().hex[:12]}",
                        "name": function.get("name", ""),
                        "input": args,
                    }
                )
            chat_messages.append(
                cast(MessageParam, {"role": "assistant", "content": content_blocks})
            )
            continue

        if role == "tool":
            chat_messages.append(
                cast(
                    MessageParam,
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": message.get("tool_call_id", ""),
                                "content": message.get("content", ""),
                            }
                        ],
                    },
                )
            )
            continue

        if role in {"user", "assistant"}:
            chat_messages.append(
                MessageParam(role=role, content=str(message.get("content", "")))
            )

    system = "\n\n".join(system_parts) if system_parts else None
    return system, chat_messages


def _to_anthropic_tools(tools: list[dict[str, object]]) -> list[dict[str, object]]:
    anthropic_tools: list[dict[str, object]] = []
    for tool in tools:
        if tool.get("type") != "function":
            continue
        function = tool.get("function")
        if not isinstance(function, dict):
            continue
        anthropic_tools.append(
            {
                "name": function.get("name", ""),
                "description": function.get("description", ""),
                "input_schema": function.get("parameters", {"type": "object"}),
            }
        )
    return anthropic_tools


def _extract_text_from_message(response: Message) -> str:
    collected: list[str] = []
    for block in response.content:
        if getattr(block, "type", None) == "text":
            text = getattr(block, "text", None)
            if isinstance(text, str) and text:
                collected.append(text)
    return "".join(collected)


def _extract_stream_delta(event: Any) -> tuple[str, str | None] | None:
    if getattr(event, "type", None) != "content_block_delta":
        if getattr(event, "type", None) == "message_delta":
            stop_reason = getattr(getattr(event, "delta", None), "stop_reason", None)
            return "", stop_reason
        if getattr(event, "type", None) == "message_stop":
            return None
        return None

    delta = getattr(event, "delta", None)
    if getattr(delta, "type", None) != "text_delta":
        return None

    return getattr(delta, "text", "") or "", None


class AnthropicProvider:
    """LLMProvider adapter backed by Anthropic Messages API."""

    def __init__(self, api_key: str | None) -> None:
        self._client = AsyncAnthropic(api_key=api_key)

    async def stream_chat(
        self,
        messages: list[ChatMessageSchema],
        model: str,
        temperature: float = 0.7,
    ) -> AsyncIterator[ProviderChunk]:
        system, anthropic_messages = _split_messages_for_anthropic(messages)
        request_payload: dict[str, Any] = {
            "model": model,
            "messages": anthropic_messages,
            "max_tokens": ANTHROPIC_MAX_TOKENS,
            "temperature": temperature,
        }
        if system is not None:
            request_payload["system"] = system

        async with self._client.messages.stream(**request_payload) as stream:
            async for event in stream:
                extracted = _extract_stream_delta(event)
                if extracted is None:
                    continue

                content, finish_reason = extracted
                if not content and finish_reason is None:
                    continue

                yield ProviderChunk(content=content, finish_reason=finish_reason)

    async def complete_chat(
        self,
        messages: list[ChatMessageSchema],
        model: str,
        temperature: float = 0.7,
    ) -> ProviderCompletion:
        system, anthropic_messages = _split_messages_for_anthropic(messages)
        request_payload: dict[str, Any] = {
            "model": model,
            "messages": anthropic_messages,
            "max_tokens": ANTHROPIC_MAX_TOKENS,
            "temperature": temperature,
        }
        if system is not None:
            request_payload["system"] = system

        response = cast(Message, await self._client.messages.create(**request_payload))
        return ProviderCompletion(
            content=_extract_text_from_message(response),
            finish_reason=getattr(response, "stop_reason", None),
            usage=_usage_from_message(response),
        )

    async def complete_chat_with_tools(
        self,
        messages: list[ChatMessageInput],
        model: str,
        tools: list[dict[str, object]],
        temperature: float = 0.7,
    ) -> ProviderToolCompletion:
        system, anthropic_messages = _split_tool_messages_for_anthropic(messages)
        request_payload: dict[str, Any] = {
            "model": model,
            "messages": anthropic_messages,
            "max_tokens": ANTHROPIC_MAX_TOKENS,
            "temperature": temperature,
        }
        if system is not None:
            request_payload["system"] = system
        anthropic_tools = _to_anthropic_tools(tools)
        if anthropic_tools:
            request_payload["tools"] = anthropic_tools

        response = cast(Message, await self._client.messages.create(**request_payload))
        tool_calls: list[ProviderToolCall] = []
        text_parts: list[str] = []
        for block in response.content:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                text = getattr(block, "text", None)
                if isinstance(text, str) and text:
                    text_parts.append(text)
                continue
            if block_type != "tool_use":
                continue
            raw_input = getattr(block, "input", None)
            arguments = raw_input if isinstance(raw_input, dict) else {}
            tool_calls.append(
                ProviderToolCall(
                    id=getattr(block, "id", None) or f"tool_{uuid.uuid4().hex[:12]}",
                    name=getattr(block, "name", "") or "",
                    arguments=cast(dict[str, object], arguments),
                )
            )

        content = "".join(text_parts) or None
        return ProviderToolCompletion(
            content=content,
            tool_calls=tool_calls,
            finish_reason=getattr(response, "stop_reason", None),
            usage=_usage_from_message(response),
        )
