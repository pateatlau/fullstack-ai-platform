"""SSE frame mapping for agent stream events (Phase 5; consumed by Phase 11 adapter)."""

from __future__ import annotations

from app.ai.agent.models.events import (
    AgentStreamEvent,
    AgentStreamEventType,
    CompleteEventPayload,
    ErrorEventPayload,
    StartEventPayload,
    TokenEventPayload,
    ToolEndEventPayload,
    ToolStartEventPayload,
)
from app.schemas.chat import (
    DeltaFrame,
    EndFrame,
    ErrorFrame,
    StartFrame,
    ToolEndFrame,
    ToolStartFrame,
)

SseMappableFrame = (
    StartFrame | DeltaFrame | EndFrame | ErrorFrame | ToolStartFrame | ToolEndFrame
)

# Agent-only events (planning, reflection) have no V1.1 SSE frame equivalent.
_NON_SSE_EVENT_TYPES = frozenset(
    {
        AgentStreamEventType.PLANNING,
        AgentStreamEventType.REFLECTION,
    }
)


def sse_frame_from_agent_event(
    event: AgentStreamEvent,
    *,
    response_id: str | None = None,
) -> tuple[str, SseMappableFrame] | None:
    """Map an agent stream event to an SSE event name and chat frame model.

    Returns ``None`` for agent-internal events that do not map to V1.1 SSE
    frames (``planning``, ``reflection``). Does not call ``format_sse`` —
    transport formatting stays in the chat adapter layer.
    """
    if event.type in _NON_SSE_EVENT_TYPES:
        return None

    frame_id = response_id or event.execution_id

    if event.type == AgentStreamEventType.START:
        payload = StartEventPayload.model_validate(event.payload)
        return (
            "start",
            StartFrame(id=frame_id, session_id=payload.session_id),
        )

    if event.type == AgentStreamEventType.TOKEN:
        payload = TokenEventPayload.model_validate(event.payload)
        return (
            "delta",
            DeltaFrame(id=frame_id, content=payload.content),
        )

    if event.type == AgentStreamEventType.TOOL_START:
        payload = ToolStartEventPayload.model_validate(event.payload)
        return (
            "tool_start",
            ToolStartFrame(
                id=frame_id,
                tool_name=payload.tool_name,
                call_id=payload.call_id,
            ),
        )

    if event.type == AgentStreamEventType.TOOL_END:
        payload = ToolEndEventPayload.model_validate(event.payload)
        return (
            "tool_end",
            ToolEndFrame(
                id=frame_id,
                tool_name=payload.tool_name,
                call_id=payload.call_id,
                success=payload.success,
            ),
        )

    if event.type == AgentStreamEventType.COMPLETE:
        payload = CompleteEventPayload.model_validate(event.payload)
        return (
            "end",
            EndFrame(id=frame_id, finish_reason=payload.finish_reason),
        )

    if event.type == AgentStreamEventType.ERROR:
        payload = ErrorEventPayload.model_validate(event.payload)
        return (
            "error",
            ErrorFrame(
                id=frame_id,
                code=payload.code,
                message=payload.message,
            ),
        )

    return None
