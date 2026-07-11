import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import AsyncIterator, Protocol, cast

from fastapi import Request

from app.core.config import Settings, get_settings
from app.providers.base import LLMProvider, ProviderChunk
from app.providers.factory import ProviderFactory
from app.schemas.chat import (
    ChatRequestSchema,
    ChatResponseSchema,
    DeltaFrame,
    EndFrame,
    ErrorFrame,
    ProviderName,
    StartFrame,
)

logger = logging.getLogger(__name__)


class ClosableAsyncIterator(Protocol):
    def __aiter__(self) -> AsyncIterator[ProviderChunk]: ...

    async def __anext__(self) -> ProviderChunk: ...

    async def aclose(self) -> None: ...


class ChatServiceError(Exception):
    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class ProviderTimeoutError(ChatServiceError):
    def __init__(self) -> None:
        super().__init__(
            code="provider_timeout",
            message="Upstream provider timed out.",
            status_code=504,
        )


class ProviderRateLimitedError(ChatServiceError):
    def __init__(self) -> None:
        super().__init__(
            code="provider_rate_limited",
            message="Upstream rate limit hit, please retry shortly.",
            status_code=429,
        )


class ProviderError(ChatServiceError):
    def __init__(self) -> None:
        super().__init__(
            code="provider_error",
            message="Upstream provider failed.",
            status_code=502,
        )


def normalize_chat_error(exc: Exception) -> ChatServiceError:
    if isinstance(exc, ChatServiceError):
        return exc

    error_name = exc.__class__.__name__.lower()
    if isinstance(exc, TimeoutError) or "timeout" in error_name:
        return ProviderTimeoutError()
    if any(
        token in error_name
        for token in (
            "ratelimit",
            "too_many_requests",
            "toomanyrequests",
            "resourceexhausted",
        )
    ):
        return ProviderRateLimitedError()
    return ProviderError()


def _format_sse(
    event: str, data: StartFrame | DeltaFrame | EndFrame | ErrorFrame
) -> str:
    return f"event: {event}\ndata: {data.model_dump_json()}\n\n"


class ChatService:
    """Validates/normalizes chat requests and orchestrates provider calls."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def _resolve_provider(
        self, request: ChatRequestSchema
    ) -> tuple[LLMProvider, str, ProviderName]:
        provider_name: ProviderName = request.provider or self._settings.llm_provider  # type: ignore[assignment]
        provider = ProviderFactory.get_provider(provider_name, self._settings)
        model = request.model or self._default_model(provider_name)
        return provider, model, provider_name

    def _default_model(self, provider_name: str) -> str:
        if provider_name == "gemini":
            return self._settings.gemini_model
        return self._settings.openai_model

    async def _complete_with_timeout(
        self,
        provider: LLMProvider,
        request: ChatRequestSchema,
        model: str,
    ) -> str:
        return await asyncio.wait_for(
            provider.complete_chat(request.messages, model, request.temperature),
            timeout=self._settings.request_timeout_seconds,
        )

    async def complete_chat(self, request: ChatRequestSchema) -> ChatResponseSchema:
        provider, model, provider_name = self._resolve_provider(request)
        try:
            content = await self._complete_with_timeout(provider, request, model)
        except Exception as exc:  # noqa: BLE001 - normalize upstream/provider failures
            raise normalize_chat_error(exc) from exc

        return ChatResponseSchema(
            id=f"resp_{uuid.uuid4().hex[:12]}",
            content=content,
            model=model,
            provider=provider_name,
        )

    async def stream_chat(
        self, request: ChatRequestSchema, http_request: Request
    ) -> AsyncIterator[str]:
        """SSE event generator: yields start -> delta* -> end (or error).

        Checks `http_request.is_disconnected()` between chunks so a client
        abort stops iterating the provider generator early.
        """
        provider, model, _ = self._resolve_provider(request)
        response_id = f"resp_{uuid.uuid4().hex[:12]}"
        provider_stream: AsyncIterator[ProviderChunk] | None = None
        closable_provider_stream: ClosableAsyncIterator | None = None

        yield _format_sse("start", StartFrame(id=response_id))

        try:
            provider_stream = provider.stream_chat(
                request.messages, model, request.temperature
            ).__aiter__()
            closable_provider_stream = cast(
                ClosableAsyncIterator | None, provider_stream
            )
            finish_reason = "stop"
            while True:
                if await http_request.is_disconnected():
                    logger.info(
                        "Client disconnected, stopping stream for %s", response_id
                    )
                    return

                try:
                    chunk = await asyncio.wait_for(
                        anext(provider_stream),
                        timeout=self._settings.request_timeout_seconds,
                    )
                except StopAsyncIteration:
                    break

                if chunk["content"]:
                    yield _format_sse(
                        "delta", DeltaFrame(id=response_id, content=chunk["content"])
                    )
                if chunk["finish_reason"]:
                    finish_reason = chunk["finish_reason"]

            yield _format_sse(
                "end", EndFrame(id=response_id, finish_reason=finish_reason)
            )
        except (
            Exception
        ) as exc:  # noqa: BLE001 - normalize any provider failure into an error frame
            app_error = normalize_chat_error(exc)
            logger.exception("Provider stream failed for %s", response_id)
            yield _format_sse(
                "error",
                ErrorFrame(
                    id=response_id,
                    code=app_error.code,
                    message=app_error.message,
                ),
            )
        finally:
            if closable_provider_stream is not None:
                close_stream = cast(
                    Callable[[], Awaitable[None]] | None,
                    getattr(closable_provider_stream, "aclose", None),
                )
                if callable(close_stream):
                    await close_stream()
