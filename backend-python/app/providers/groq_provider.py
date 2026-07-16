from typing import Any, AsyncIterator, cast

from groq import AsyncGroq, AsyncStream
from groq.types.chat import (
    ChatCompletion,
    ChatCompletionChunk,
    ChatCompletionMessageParam,
)

from app.providers.base import ProviderChunk
from app.schemas.chat import ChatMessageSchema


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
        stream: AsyncStream[ChatCompletionChunk] = (
            await self._client.chat.completions.create(
                model=model,
                messages=_to_groq_messages(messages),
                temperature=temperature,
                stream=True,
            )
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
    ) -> str:
        response: ChatCompletion = await self._client.chat.completions.create(
            model=model,
            messages=_to_groq_messages(messages),
            temperature=temperature,
            stream=False,
        )

        if not response.choices:
            return ""

        return _coerce_message_content(response.choices[0].message.content)
