import logging
import uuid
from typing import AsyncIterator

from fastapi import Request

from app.core.config import Settings, get_settings
from app.providers.base import LLMProvider
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

    async def complete_chat(self, request: ChatRequestSchema) -> ChatResponseSchema:
        provider, model, provider_name = self._resolve_provider(request)
        content = await provider.complete_chat(
            request.messages, model, request.temperature
        )
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

        yield _format_sse("start", StartFrame(id=response_id))

        try:
            finish_reason = "stop"
            async for chunk in provider.stream_chat(
                request.messages, model, request.temperature
            ):
                if await http_request.is_disconnected():
                    logger.info(
                        "Client disconnected, stopping stream for %s", response_id
                    )
                    return

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
            logger.exception("Provider stream failed for %s", response_id)
            yield _format_sse(
                "error",
                ErrorFrame(id=response_id, code="provider_error", message=str(exc)),
            )
