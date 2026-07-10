from typing import AsyncIterator, Protocol, TypedDict

from app.schemas.chat import ChatMessageSchema


class ProviderChunk(TypedDict):
    content: str
    finish_reason: str | None


class LLMProvider(Protocol):
    """Contract every provider adapter (OpenAI, Gemini, ...) must implement.

    `ChatService` and the routers only ever depend on this interface, never
    on a concrete provider SDK.
    """

    def stream_chat(
        self,
        messages: list[ChatMessageSchema],
        model: str,
        temperature: float = 0.7,
    ) -> AsyncIterator[ProviderChunk]: ...

    async def complete_chat(
        self,
        messages: list[ChatMessageSchema],
        model: str,
        temperature: float = 0.7,
    ) -> str: ...
