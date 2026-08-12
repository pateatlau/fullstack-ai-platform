"""Pydantic schemas for the tool execution platform."""

from __future__ import annotations

import uuid
from typing import Any, TypedDict

from pydantic import BaseModel, Field

from app.core.caller import CallerContext


class ToolDefinition(BaseModel):
    """Registered tool metadata for callers and the tool platform.

    ``name``, ``description``, and ``parameters`` are exposed to LLM
    function-calling APIs via ``ToolRegistry.get_schemas_for_llm()``.
    ``requires_approval``, ``category``, ``risk_level``, and
    ``data_sensitivity`` are platform-only HITL metadata (see
    ``app.ai.hitl.rules``) and are intentionally omitted from LLM schemas.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    requires_approval: bool = False
    # Optional HITL rule-engine inputs (recommendation #1). Left unset, tools
    # simply do not match category/risk/sensitivity-based rule conditions.
    category: str | None = None
    risk_level: str | None = None
    data_sensitivity: str | None = None


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


class TrustedPolicyKwargs(TypedDict):
    """Server-trusted fields forwarded to :class:`~app.ai.hitl.policy.ApprovalPolicy`."""

    caller_role: str
    workspace: str | None
    tenant: str | None
    estimated_cost: float | None


class ToolExecutionContext(BaseModel):
    """Portable execution context for authorization and structured logging."""

    caller: CallerContext
    request_id: str | None = None
    session_id: uuid.UUID | None = None
    # Stable per-attempt id (``{run_id}:{node_id}:{attempt}``) set by the Workflow
    # Engine (Epic 06) so receipt-aware tools can detect and dedupe replayed
    # invocations after crash recovery (Part I § Crash-safe running). Unused by
    # ToolExecutor itself and ``None`` outside workflow node execution.
    execution_receipt_id: str | None = None
    approval_correlation_id: uuid.UUID | None = None
    # Trusted HITL rule-engine inputs (recommendation #1). Populated by the
    # server from request/agent metadata — never from LLM tool arguments.
    workspace: str | None = None
    tenant: str | None = None
    estimated_cost: float | None = None

    model_config = {"arbitrary_types_allowed": True}

    def trusted_policy_kwargs(self) -> TrustedPolicyKwargs:
        """Return server-trusted fields forwarded to :class:`ApprovalPolicy`."""
        return {
            "caller_role": self.caller.kind,
            "workspace": self.workspace,
            "tenant": self.tenant,
            "estimated_cost": self.estimated_cost,
        }
