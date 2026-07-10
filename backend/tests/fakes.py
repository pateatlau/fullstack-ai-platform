import asyncio
from typing import AsyncIterator

from app.providers.base import ProviderChunk
from app.schemas.chat import ChatMessageSchema


class FakeProvider:
    """Deterministic `LLMProvider` test double — no network calls.

    Streams the words of a fixed (or injected) response one at a time so
    endpoint/streaming tests can assert on chunk sequencing without hitting
    a real LLM API.
    """

    def __init__(self, response: str = "Hello from the fake provider.") -> None:
        self.response = response

    async def stream_chat(
        self,
        messages: list[ChatMessageSchema],
        model: str,
        temperature: float = 0.7,
    ) -> AsyncIterator[ProviderChunk]:
        words = self.response.split(" ")
        for i, word in enumerate(words):
            await asyncio.sleep(
                0.05
            )  # simulate token pacing for manual SSE verification
            is_last = i == len(words) - 1
            content = word if is_last else f"{word} "
            yield ProviderChunk(
                content=content,
                finish_reason="stop" if is_last else None,
            )

    async def complete_chat(
        self,
        messages: list[ChatMessageSchema],
        model: str,
        temperature: float = 0.7,
    ) -> str:
        return self.response
