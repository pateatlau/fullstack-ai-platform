import asyncio
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, cast

from google import genai

from app.providers.base import ProviderChunk
from app.schemas.chat import ChatMessageSchema


def _message_to_line(message: ChatMessageSchema) -> str:
    return f"{message.role}: {message.content}"


def _messages_to_prompt(messages: list[ChatMessageSchema]) -> str:
    return "\n".join(_message_to_line(message) for message in messages)


def _extract_text(payload: Any) -> str:
    text = getattr(payload, "text", None)
    if isinstance(text, str) and text:
        return text

    candidates = getattr(payload, "candidates", None)
    if not candidates:
        return ""

    parts = getattr(candidates[0].content, "parts", [])
    collected: list[str] = []
    for part in parts:
        part_text = getattr(part, "text", None)
        if part_text:
            collected.append(part_text)

    return "".join(collected)


@dataclass
class _NextChunkResult:
    done: bool
    payload: Any | None


def _next_chunk(iterator: Iterator[Any]) -> _NextChunkResult:
    try:
        return _NextChunkResult(done=False, payload=next(iterator))
    except StopIteration:
        return _NextChunkResult(done=True, payload=None)


class GeminiProvider:
    """LLMProvider adapter backed by Gemini via the google-genai SDK."""

    def __init__(self, api_key: str | None) -> None:
        self._client = genai.Client(api_key=api_key)

    def _generate_content_stream(
        self,
        *,
        model: str,
        prompt: str,
        temperature: float,
    ) -> Iterator[Any]:
        # google-genai's type stubs are broad; this wrapper keeps strict
        # type-checkers happy while preserving the SDK call shape.
        models_api = cast(Any, self._client.models)
        generate_content_stream = cast(
            Callable[..., Iterator[Any]],
            models_api.generate_content_stream,
        )
        return generate_content_stream(
            model=model,
            contents=prompt,
            config={"temperature": temperature},
        )

    def _generate_content(
        self,
        *,
        model: str,
        prompt: str,
        temperature: float,
    ) -> Any:
        models_api = cast(Any, self._client.models)
        generate_content = cast(
            Callable[..., Any],
            models_api.generate_content,
        )
        return generate_content(
            model=model,
            contents=prompt,
            config={"temperature": temperature},
        )

    async def stream_chat(
        self,
        messages: list[ChatMessageSchema],
        model: str,
        temperature: float = 0.7,
    ) -> AsyncIterator[ProviderChunk]:
        prompt = _messages_to_prompt(messages)
        stream = self._generate_content_stream(
            model=model,
            prompt=prompt,
            temperature=temperature,
        )

        iterator = iter(stream)
        while True:
            result = await asyncio.to_thread(_next_chunk, iterator)
            if result.done:
                break

            content = _extract_text(result.payload)
            if content:
                yield ProviderChunk(content=content, finish_reason=None)

    async def complete_chat(
        self,
        messages: list[ChatMessageSchema],
        model: str,
        temperature: float = 0.7,
    ) -> str:
        prompt = _messages_to_prompt(messages)
        response = await asyncio.to_thread(
            self._generate_content,
            model=model,
            prompt=prompt,
            temperature=temperature,
        )
        return _extract_text(response)
