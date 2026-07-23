"""Streaming chat adapter for the agent runtime (Phase 11)."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator

from fastapi import Request

from app.ai.agent.adapters.chat_adapter import (
    build_agent_context,
    build_agent_request,
)
from app.ai.agent.models.events import AgentStreamEventType, ErrorEventPayload
from app.ai.agent.runtime.default_agent import DefaultAgent
from app.ai.agent.streaming.adapter import sse_frame_from_agent_event
from app.core.caller import CallerContext
from app.core.config import Settings
from app.core.logging import get_logger
from app.providers.base import LLMProvider
from app.schemas.chat import (
    ChatRequestSchema,
    DeltaFrame,
    EndFrame,
    ErrorFrame,
    ProviderName,
    StartFrame,
)
from app.services.chat_service import (
    ChatService,
    EmptyProviderResponseError,
    _StreamPrep,
    format_sse,
    normalize_chat_error,
)

logger = get_logger(__name__)


async def stream_agent_chat(
    *,
    agent: DefaultAgent,
    chat_service: ChatService,
    settings: Settings,
    request: ChatRequestSchema,
    http_request: Request,
    caller: CallerContext | None,
    prep: _StreamPrep | None,
    response_id: str,
    session_id: uuid.UUID | None,
    provider: LLMProvider,
    provider_name: ProviderName,
    model: str,
    allowed_tool_names: frozenset[str] | None = None,
    request_start_time: float | None = None,
    retrieval_latency_ms: int | None = None,
) -> AsyncIterator[str]:
    """Yield SSE frames from an agent stream with V1.1 web-search parity."""
    agent_request = build_agent_request(
        request=request,
        model=model,
        provider_name=provider_name,
        caller=caller,
        settings=settings,
        allowed_tool_names=allowed_tool_names,
    )
    agent_context = build_agent_context(
        caller=caller,
        allowed_tool_names=allowed_tool_names,
    )

    collected: list[str] = []
    finish_reason = "stop"
    start_emitted = False
    tool_rounds = 0
    stream_start = time.perf_counter()
    first_delta_logged = False

    def _emit_start() -> str:
        nonlocal start_emitted
        start_emitted = True
        return format_sse(
            "start",
            StartFrame(id=response_id, session_id=session_id),
        )

    try:
        async for event in agent.stream(agent_request, agent_context):
            if await http_request.is_disconnected():
                logger.info(
                    "Client disconnected during agent stream",
                    response_id=response_id,
                )
                await chat_service._persist_stream_result(
                    caller=caller,
                    prep=prep,
                    provider=provider,
                    provider_name=provider_name,
                    model=model,
                    content="".join(collected),
                    finish_reason="interrupted",
                    status="interrupted",
                )
                return

            if event.type in {
                AgentStreamEventType.START,
                AgentStreamEventType.PLANNING,
                AgentStreamEventType.REFLECTION,
            }:
                continue

            if event.type == AgentStreamEventType.TOOL_START:
                tool_rounds += 1
                mapped = sse_frame_from_agent_event(event, response_id=response_id)
                if mapped is not None:
                    event_name, frame = mapped
                    yield format_sse(event_name, frame)
                continue

            if event.type == AgentStreamEventType.TOOL_END:
                mapped = sse_frame_from_agent_event(event, response_id=response_id)
                if mapped is not None:
                    event_name, frame = mapped
                    yield format_sse(event_name, frame)
                continue

            if event.type == AgentStreamEventType.TOKEN:
                payload = event.typed_payload()
                content = getattr(payload, "content", "")
                if not start_emitted:
                    yield _emit_start()
                if content:
                    if not first_delta_logged and request_start_time is not None:
                        time_to_first_delta_ms = int(
                            (time.perf_counter() - request_start_time) * 1000
                        )
                        logger.info(
                            "Agent stream first delta",
                            response_id=response_id,
                            time_to_first_delta_ms=time_to_first_delta_ms,
                            retrieval_latency_ms=retrieval_latency_ms,
                        )
                        first_delta_logged = True
                    collected.append(content)
                    yield format_sse(
                        "delta",
                        DeltaFrame(id=response_id, content=content),
                    )
                continue

            if event.type == AgentStreamEventType.COMPLETE:
                payload = event.typed_payload()
                finish_reason = getattr(payload, "finish_reason", "stop")
                if not start_emitted:
                    yield _emit_start()

                if not collected:
                    empty_error = EmptyProviderResponseError()
                    logger.warning(
                        "Agent stream returned no content",
                        provider=provider_name,
                        model=model,
                        response_id=response_id,
                        finish_reason=finish_reason,
                    )
                    await chat_service._persist_stream_result(
                        caller=caller,
                        prep=prep,
                        provider=provider,
                        provider_name=provider_name,
                        model=model,
                        content="",
                        finish_reason=None,
                        status="error",
                    )
                    yield format_sse(
                        "error",
                        ErrorFrame(
                            id=response_id,
                            code=empty_error.code,
                            message=empty_error.message,
                        ),
                    )
                    return

                await chat_service._persist_stream_result(
                    caller=caller,
                    prep=prep,
                    provider=provider,
                    provider_name=provider_name,
                    model=model,
                    content="".join(collected),
                    finish_reason=finish_reason,
                    status="complete",
                )
                latency_ms = int((time.perf_counter() - stream_start) * 1000)
                logger.info(
                    "Agent chat stream completed",
                    provider=provider_name,
                    model=model,
                    latency_ms=latency_ms,
                    response_id=response_id,
                    stream_tool_rounds=tool_rounds,
                    retrieval_latency_ms=retrieval_latency_ms,
                )
                yield format_sse(
                    "end",
                    EndFrame(id=response_id, finish_reason=finish_reason),
                )
                return

            if event.type == AgentStreamEventType.ERROR:
                if not start_emitted:
                    yield _emit_start()
                mapped = sse_frame_from_agent_event(event, response_id=response_id)
                if mapped is not None:
                    event_name, frame = mapped
                else:
                    event_name = "error"
                    try:
                        payload = event.typed_payload()
                        if isinstance(payload, ErrorEventPayload):
                            code = payload.code
                            message = payload.message
                        else:
                            code = "agent_error"
                            message = "Agent execution failed."
                    except Exception:
                        code = "agent_error"
                        message = "Agent execution failed."
                    frame = ErrorFrame(id=response_id, code=code, message=message)
                await chat_service._persist_stream_result(
                    caller=caller,
                    prep=prep,
                    provider=provider,
                    provider_name=provider_name,
                    model=model,
                    content="".join(collected),
                    finish_reason=None,
                    status="error",
                )
                yield format_sse(event_name, frame)
                return

    except Exception as exc:  # noqa: BLE001 - normalize provider failures
        app_error = normalize_chat_error(exc)
        logger.exception(
            "Agent chat stream failed",
            response_id=response_id,
            provider=provider_name,
            model=model,
        )
        if not start_emitted:
            yield _emit_start()
        try:
            await chat_service._persist_stream_result(
                caller=caller,
                prep=prep,
                provider=provider,
                provider_name=provider_name,
                model=model,
                content="".join(collected),
                finish_reason=None,
                status="error",
            )
        except Exception:  # noqa: BLE001 - best-effort error persistence
            logger.exception(
                "Failed to persist agent stream error state",
                response_id=response_id,
            )
        yield format_sse(
            "error",
            ErrorFrame(
                id=response_id,
                code=app_error.code,
                message=app_error.message,
            ),
        )
