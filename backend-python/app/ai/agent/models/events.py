"""Stream event models for the agent runtime (Phase 5)."""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class AgentStreamEventType(StrEnum):
    """High-level event categories emitted during agent execution."""

    START = "start"
    PLANNING = "planning"
    TOOL_START = "tool_start"
    TOOL_END = "tool_end"
    TOKEN = "token"
    REFLECTION = "reflection"
    COMPLETE = "complete"
    ERROR = "error"


class StartEventPayload(BaseModel):
    """Payload for :attr:`AgentStreamEventType.START`."""

    session_id: uuid.UUID | None = None


class PlanningEventPayload(BaseModel):
    """Payload for :attr:`AgentStreamEventType.PLANNING`."""

    iteration: int = Field(ge=0)


class ToolStartEventPayload(BaseModel):
    """Payload for :attr:`AgentStreamEventType.TOOL_START`."""

    tool_name: str = Field(min_length=1)
    call_id: str = Field(min_length=1)


class ToolEndEventPayload(BaseModel):
    """Payload for :attr:`AgentStreamEventType.TOOL_END`."""

    tool_name: str = Field(min_length=1)
    call_id: str = Field(min_length=1)
    success: bool


class TokenEventPayload(BaseModel):
    """Payload for :attr:`AgentStreamEventType.TOKEN` (sanitized token chunk)."""

    content: str


class ReflectionDecision(StrEnum):
    """Reflection engine outcomes (Part I § Reflection rules)."""

    REPLAN = "REPLAN"
    RETRY_STEP = "RETRY_STEP"
    CONTINUE = "CONTINUE"
    FINISH = "FINISH"


class ReflectionEventPayload(BaseModel):
    """Payload for :attr:`AgentStreamEventType.REFLECTION`."""

    decision: ReflectionDecision
    reason: str | None = None


class CompleteEventPayload(BaseModel):
    """Payload for :attr:`AgentStreamEventType.COMPLETE`."""

    finish_reason: str = "stop"
    tools_used: list[str] = Field(default_factory=list)


class ErrorEventPayload(BaseModel):
    """Payload for :attr:`AgentStreamEventType.ERROR`."""

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)


AgentStreamEventPayload = (
    StartEventPayload
    | PlanningEventPayload
    | ToolStartEventPayload
    | ToolEndEventPayload
    | TokenEventPayload
    | ReflectionEventPayload
    | CompleteEventPayload
    | ErrorEventPayload
)


_PAYLOAD_MODEL_BY_TYPE: dict[
    AgentStreamEventType,
    type[AgentStreamEventPayload],
] = {
    AgentStreamEventType.START: StartEventPayload,
    AgentStreamEventType.PLANNING: PlanningEventPayload,
    AgentStreamEventType.TOOL_START: ToolStartEventPayload,
    AgentStreamEventType.TOOL_END: ToolEndEventPayload,
    AgentStreamEventType.TOKEN: TokenEventPayload,
    AgentStreamEventType.REFLECTION: ReflectionEventPayload,
    AgentStreamEventType.COMPLETE: CompleteEventPayload,
    AgentStreamEventType.ERROR: ErrorEventPayload,
}


class AgentStreamEvent(BaseModel):
    """Typed progress event published via :class:`StreamPublisher`."""

    type: AgentStreamEventType
    execution_id: str = Field(min_length=1)
    payload: dict[str, object] = Field(default_factory=dict)

    def typed_payload(self) -> AgentStreamEventPayload:
        """Validate and parse ``payload`` for this event's ``type``."""
        model = _PAYLOAD_MODEL_BY_TYPE[self.type]
        return model.model_validate(self.payload)

    @classmethod
    def start(
        cls,
        execution_id: str,
        *,
        session_id: uuid.UUID | None = None,
    ) -> AgentStreamEvent:
        payload = StartEventPayload(session_id=session_id)
        return cls(
            type=AgentStreamEventType.START,
            execution_id=execution_id,
            payload=payload.model_dump(mode="json"),
        )

    @classmethod
    def planning(cls, execution_id: str, *, iteration: int) -> AgentStreamEvent:
        payload = PlanningEventPayload(iteration=iteration)
        return cls(
            type=AgentStreamEventType.PLANNING,
            execution_id=execution_id,
            payload=payload.model_dump(),
        )

    @classmethod
    def tool_start(
        cls,
        execution_id: str,
        *,
        tool_name: str,
        call_id: str,
    ) -> AgentStreamEvent:
        payload = ToolStartEventPayload(tool_name=tool_name, call_id=call_id)
        return cls(
            type=AgentStreamEventType.TOOL_START,
            execution_id=execution_id,
            payload=payload.model_dump(),
        )

    @classmethod
    def tool_end(
        cls,
        execution_id: str,
        *,
        tool_name: str,
        call_id: str,
        success: bool,
    ) -> AgentStreamEvent:
        payload = ToolEndEventPayload(
            tool_name=tool_name,
            call_id=call_id,
            success=success,
        )
        return cls(
            type=AgentStreamEventType.TOOL_END,
            execution_id=execution_id,
            payload=payload.model_dump(),
        )

    @classmethod
    def token(cls, execution_id: str, *, content: str) -> AgentStreamEvent:
        payload = TokenEventPayload(content=content)
        return cls(
            type=AgentStreamEventType.TOKEN,
            execution_id=execution_id,
            payload=payload.model_dump(),
        )

    @classmethod
    def reflection(
        cls,
        execution_id: str,
        *,
        decision: ReflectionDecision
        | Literal["REPLAN", "RETRY_STEP", "CONTINUE", "FINISH"],
        reason: str | None = None,
    ) -> AgentStreamEvent:
        payload = ReflectionEventPayload(
            decision=ReflectionDecision(decision), reason=reason
        )
        return cls(
            type=AgentStreamEventType.REFLECTION,
            execution_id=execution_id,
            payload=payload.model_dump(),
        )

    @classmethod
    def complete(
        cls,
        execution_id: str,
        *,
        finish_reason: str = "stop",
        tools_used: list[str] | None = None,
    ) -> AgentStreamEvent:
        payload = CompleteEventPayload(
            finish_reason=finish_reason,
            tools_used=tools_used or [],
        )
        return cls(
            type=AgentStreamEventType.COMPLETE,
            execution_id=execution_id,
            payload=payload.model_dump(),
        )

    @classmethod
    def error(
        cls,
        execution_id: str,
        *,
        code: str,
        message: str,
    ) -> AgentStreamEvent:
        payload = ErrorEventPayload(code=code, message=message)
        return cls(
            type=AgentStreamEventType.ERROR,
            execution_id=execution_id,
            payload=payload.model_dump(),
        )
