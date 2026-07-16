from typing import Any, AsyncIterator, cast

from anthropic import AsyncAnthropic
from anthropic.types import Message, MessageParam

from app.providers.base import ProviderChunk
from app.schemas.chat import ChatMessageSchema

ANTHROPIC_MAX_TOKENS = 1024


def _split_messages_for_anthropic(
    messages: list[ChatMessageSchema],
) -> tuple[str | None, list[MessageParam]]:
    system_parts: list[str] = []
    chat_messages: list[MessageParam] = []

    for message in messages:
        if message.role == "system":
            system_parts.append(message.content)
            continue

        chat_messages.append(MessageParam(role=message.role, content=message.content))

    system = "\n\n".join(system_parts) if system_parts else None
    return system, chat_messages


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
    ) -> str:
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
        return _extract_text_from_message(response)
