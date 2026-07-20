from typing import Any, AsyncIterator, cast

from groq import AsyncGroq, AsyncStream
from groq.types.chat import (
    ChatCompletion,
    ChatCompletionChunk,
    ChatCompletionMessageParam,
)

from app.providers.base import (
    ChatMessageInput,
    ProviderChunk,
    ProviderCompletion,
    ProviderToolCompletion,
    ProviderUsage,
)
from app.schemas.chat import ChatMessageSchema


def _usage_from_response(response: Any) -> ProviderUsage | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    return ProviderUsage(
        prompt_tokens=getattr(usage, "prompt_tokens", None),
        completion_tokens=getattr(usage, "completion_tokens", None),
        total_tokens=getattr(usage, "total_tokens", None),
    )


def _to_groq_messages(
    messages: list[ChatMessageSchema],
) -> list[ChatCompletionMessageParam]:
    return cast(
        list[ChatCompletionMessageParam],
        [{"role": message.role, "content": message.content} for message in messages],
    )


def _coerce_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if content is None:
        return ""

    collected: list[str] = []
    for part in content:
        text = getattr(part, "text", None)
        if isinstance(text, str) and text:
            collected.append(text)
    return "".join(collected)


class GroqProvider:
    """LLMProvider adapter backed by the Groq chat completions API."""

    def __init__(self, api_key: str | None) -> None:
        self._client = AsyncGroq(api_key=api_key)

    async def stream_chat(
        self,
        messages: list[ChatMessageSchema],
        model: str,
        temperature: float = 0.7,
    ) -> AsyncIterator[ProviderChunk]:
        stream: AsyncStream[
            ChatCompletionChunk
        ] = await self._client.chat.completions.create(
            model=model,
            messages=_to_groq_messages(messages),
            temperature=temperature,
            stream=True,
        )

        async for event in stream:
            if not event.choices:
                continue

            choice = event.choices[0]
            delta = getattr(choice, "delta", None)
            content = getattr(delta, "content", None) if delta is not None else None
            finish_reason = getattr(choice, "finish_reason", None)

            normalized_content = content or ""
            if not normalized_content and finish_reason is None:
                continue

            yield ProviderChunk(
                content=normalized_content,
                finish_reason=finish_reason,
            )

    async def complete_chat(
        self,
        messages: list[ChatMessageSchema],
        model: str,
        temperature: float = 0.7,
    ) -> ProviderCompletion:
        response: ChatCompletion = await self._client.chat.completions.create(
            model=model,
            messages=_to_groq_messages(messages),
            temperature=temperature,
            stream=False,
        )

        usage = _usage_from_response(response)
        if not response.choices:
            return ProviderCompletion(content="", usage=usage)

        choice = response.choices[0]
        return ProviderCompletion(
            content=_coerce_message_content(choice.message.content),
            finish_reason=getattr(choice, "finish_reason", None),
            usage=usage,
        )

    async def complete_chat_with_tools(
        self,
        messages: list[ChatMessageInput],
        model: str,
        tools: list[dict[str, object]],
        temperature: float = 0.7,
    ) -> ProviderToolCompletion:
        del messages, model, tools, temperature
        raise NotImplementedError("Tool calling is not supported for Groq in V1")
