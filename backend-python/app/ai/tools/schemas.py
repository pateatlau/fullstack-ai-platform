"""Pydantic schemas for the tool execution platform."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.core.caller import CallerContext


class ToolDefinition(BaseModel):
    """Registered tool metadata exposed to callers and LLM function-calling APIs."""

    name: str
    description: str
    parameters: dict[str, Any]


class ToolCall(BaseModel):
    """A parsed tool invocation (from an LLM response or test fixture)."""

    name: str
    arguments: dict[str, object] = Field(default_factory=dict)
    call_id: str | None = None
    # Gemini 3.x attaches this to functionCall parts; it must be echoed back
    # on the next model turn or the API rejects the request.
    thought_signature: bytes | None = None


class ToolResult(BaseModel):
    """Normalized tool execution envelope for all success and failure paths."""

    success: bool
    data: object | None = None
    error: str | None = None
    error_code: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class ToolExecutionContext(BaseModel):
    """Portable execution context for authorization and structured logging."""

    caller: CallerContext
    request_id: str | None = None
    # Stable per-attempt id (``{run_id}:{node_id}:{attempt}``) set by the Workflow
    # Engine (Epic 06) so receipt-aware tools can detect and dedupe replayed
    # invocations after crash recovery (Part I § Crash-safe running). Unused by
    # ToolExecutor itself and ``None`` outside workflow node execution.
    execution_receipt_id: str | None = None

    model_config = {"arbitrary_types_allowed": True}
