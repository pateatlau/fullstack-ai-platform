from typing import Any, AsyncIterator, cast

from openai import AsyncOpenAI, AsyncStream
from openai.types.chat import (
    ChatCompletion,
    ChatCompletionChunk,
    ChatCompletionMessageParam,
)

from app.providers.base import ProviderChunk
from app.schemas.chat import ChatMessageSchema


def _to_openai_messages(
    messages: list[ChatMessageSchema],
) -> list[ChatCompletionMessageParam]:
    # The OpenAI SDK expects a union of typed message params; our internal
    # schema uses a simpler role/content shape.
    return cast(
        list[ChatCompletionMessageParam],
        [{"role": m.role, "content": m.content} for m in messages],
    )


def _coerce_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if content is None:
        return ""

    # Some SDK variants can return structured content parts.
    collected: list[str] = []
    for part in content:
        text = getattr(part, "text", None)
        if isinstance(text, str) and text:
            collected.append(text)
    return "".join(collected)


class OpenAIProvider:
    """LLMProvider adapter backed by the OpenAI Chat Completions API."""

    def __init__(self, api_key: str | None) -> None:
        self._client = AsyncOpenAI(api_key=api_key)

    async def stream_chat(
        self,
        messages: list[ChatMessageSchema],
        model: str,
        temperature: float = 0.7,
    ) -> AsyncIterator[ProviderChunk]:
        stream: AsyncStream[ChatCompletionChunk] = (
            await self._client.chat.completions.create(
                model=model,
                messages=_to_openai_messages(messages),
                temperature=temperature,
                stream=True,
            )
        )
        async for event in stream:
            if not event.choices:
                continue
            choice = event.choices[0]
            yield ProviderChunk(
                content=choice.delta.content or "",
                finish_reason=choice.finish_reason,
            )

    async def complete_chat(
        self,
        messages: list[ChatMessageSchema],
        model: str,
        temperature: float = 0.7,
    ) -> str:
        response: ChatCompletion = await self._client.chat.completions.create(
            model=model,
            messages=_to_openai_messages(messages),
            temperature=temperature,
            stream=False,
        )
        if not response.choices:
            return ""
        return _coerce_message_content(response.choices[0].message.content)
