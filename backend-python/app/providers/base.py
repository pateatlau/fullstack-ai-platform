from dataclasses import dataclass
from typing import AsyncIterator, NotRequired, Protocol, TypedDict

from app.schemas.chat import ChatMessageSchema


@dataclass(frozen=True)
class ProviderUsage:
    """Provider-reported token counts for one generation (plan Sections 2.7, 5.7).

    Any field may be ``None`` when the provider does not report it. When a
    provider omits usage entirely, adapters return ``None`` and the app layer
    falls back to an estimate (``token_source = 'estimated'``).
    """

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True)
class ProviderCompletion:
    """Result of a non-streaming completion, including optional usage."""

    content: str
    finish_reason: str | None = None
    usage: ProviderUsage | None = None


class ProviderChunk(TypedDict):
    content: str
    finish_reason: str | None
    # Present on the terminal chunk when a provider surfaces streaming usage.
    usage: NotRequired[ProviderUsage | None]


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
    ) -> ProviderCompletion: ...
