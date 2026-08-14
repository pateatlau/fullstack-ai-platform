"""Orchestrate the tool lifecycle: registry → validation → auth → execution."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from opentelemetry.trace import Span

from app.ai.observability.tracing.spans import (
    elapsed_ms_since,
    record_hitl_tool_execution_latency_ms,
    record_tool_span_outcome,
    tool_span,
)
from app.ai.security.audit.actions import AuditAction
from app.ai.security.audit.models import AuditOutcome
from app.ai.tools.authorizer import ToolAuthorizer
from app.ai.tools.registry import ToolRegistry
from app.ai.tools.schemas import ToolCall, ToolExecutionContext, ToolResult
from app.ai.tools.validator import ToolValidator
from app.core.config import Settings
from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.ai.mcp.permissions import McpPermissionPolicy
    from app.ai.security.audit.logger import AuditLogger
    from app.ai.security.rbac.service import RbacService

_logger = get_logger(__name__)


class ToolExecutor:
    """Run tool calls through validation, authorization, execution, and normalization."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        settings: Settings,
        validator: ToolValidator | None = None,
        authorizer: ToolAuthorizer | None = None,
        mcp_permission_policy: McpPermissionPolicy | None = None,
        rbac_service: RbacService | None = None,
        audit_logger: "AuditLogger | None" = None,
    ) -> None:
        self._registry = registry
        self._settings = settings
        self._validator = validator or ToolValidator()
        self._authorizer = authorizer or ToolAuthorizer(
            rbac_service=rbac_service, settings=settings
        )
        self._mcp_permission_policy = mcp_permission_policy
        self._audit_logger = audit_logger

    async def execute(
        self,
        call: ToolCall,
        context: ToolExecutionContext,
    ) -> ToolResult:
        start = time.perf_counter()
        tool_name = call.name

        with tool_span(tool_name) as span:
            tool = self._registry.get(tool_name)
            if tool is None:
                return self._finalize(
                    call=call,
                    context=context,
                    result=ToolResult(
                        success=False,
                        error=f"Tool '{tool_name}' is not registered",
                        error_code="not_found",
                    ),
                    start=start,
                    span=span,
                )

            handler = self._registry.get_handler(tool_name)
            if handler is None:
                return self._finalize(
                    call=call,
                    context=context,
                    result=ToolResult(
                        success=False,
                        error=f"Handler for tool '{tool_name}' is not registered",
                        error_code="not_found",
                    ),
                    start=start,
                    span=span,
                )

            arguments = _normalize_handler_arguments(handler, call.arguments)
            validation_error = self._validator.validate(tool, arguments)
            if validation_error is not None:
                return self._finalize(
                    call=call,
                    context=context,
                    result=ToolResult(
                        success=False,
                        error=validation_error.message,
                        error_code="validation_error",
                    ),
                    start=start,
                    span=span,
                )

            auth_error = await self._authorizer.authorize(tool, context)
            if auth_error is not None:
                if self._audit_logger is not None:
                    await self._audit_logger.record(
                        actor=context.caller,
                        action=AuditAction.TOOL_EXECUTION_DENIED.value,
                        outcome=AuditOutcome.DENIED,
                        resource_type="tool",
                        resource_id=tool_name,
                        metadata={"reason": auth_error},
                    )
                return self._finalize(
                    call=call,
                    context=context,
                    result=ToolResult(
                        success=False,
                        error=auth_error,
                        error_code="forbidden",
                    ),
                    start=start,
                    span=span,
                    authorization_result="denied",
                )

            # Phase 7: MCP permission check (after ToolAuthorizer; both must pass)
            if self._mcp_permission_policy is not None:
                mcp_permission_error = self._check_mcp_permissions(handler)
                if mcp_permission_error is not None:
                    return self._finalize(
                        call=call,
                        context=context,
                        result=ToolResult(
                            success=False,
                            error=mcp_permission_error,
                            error_code="forbidden",
                        ),
                        start=start,
                        span=span,
                        authorization_result="denied",
                    )

            try:
                handler_result = await asyncio.wait_for(
                    handler.execute(arguments, context),
                    timeout=self._settings.request_timeout_seconds,
                )
            except TimeoutError:
                return self._finalize(
                    call=call,
                    context=context,
                    result=ToolResult(
                        success=False,
                        error="Tool execution timed out",
                        error_code="timeout",
                    ),
                    start=start,
                    span=span,
                    authorization_result="allowed",
                )
            except Exception:
                _logger.exception(
                    "Tool handler raised an exception",
                    tool_name=tool_name,
                    request_id=context.request_id,
                )
                return self._finalize(
                    call=call,
                    context=context,
                    result=ToolResult(
                        success=False,
                        error="Tool execution failed",
                        error_code="handler_error",
                    ),
                    start=start,
                    span=span,
                    authorization_result="allowed",
                )

            if not handler_result.success:
                return self._finalize(
                    call=call,
                    context=context,
                    result=handler_result,
                    start=start,
                    span=span,
                    authorization_result="allowed",
                )

            metadata = dict(handler_result.metadata)
            metadata.setdefault("tool_name", tool_name)
            return self._finalize(
                call=call,
                context=context,
                result=handler_result.model_copy(update={"metadata": metadata}),
                start=start,
                span=span,
                authorization_result="allowed",
            )

    def _finalize(
        self,
        *,
        call: ToolCall,
        context: ToolExecutionContext,
        result: ToolResult,
        start: float,
        span: Span | None = None,
        authorization_result: str | None = None,
    ) -> ToolResult:
        latency_ms = elapsed_ms_since(start)
        tool_name = call.name

        metadata = dict(result.metadata)
        metadata["tool_name"] = tool_name
        metadata["latency_ms"] = latency_ms
        if call.call_id is not None:
            metadata["call_id"] = call.call_id

        normalized = result.model_copy(update={"metadata": metadata})

        record_tool_span_outcome(
            span,
            tool_name=tool_name,
            success=normalized.success,
            latency_ms=latency_ms,
            authorization_result=authorization_result,
            approval_correlation_id=(
                str(context.approval_correlation_id)
                if context.approval_correlation_id is not None
                else None
            ),
        )
        hitl_kind = _hitl_kind_from_context(context)
        if hitl_kind is not None:
            record_hitl_tool_execution_latency_ms(
                kind=hitl_kind,
                latency_ms=latency_ms,
            )

        if normalized.success:
            _logger.info(
                "Tool execution completed",
                tool_name=tool_name,
                latency_ms=latency_ms,
                success=True,
                request_id=context.request_id,
                tool_calls_total=1,
            )
        else:
            _logger.warning(
                "Tool execution failed",
                tool_name=tool_name,
                latency_ms=latency_ms,
                success=False,
                request_id=context.request_id,
                tool_calls_total=1,
                tool_errors_total=1,
                error_code=normalized.error_code,
                error=normalized.error,
            )

        return normalized

    def _check_mcp_permissions(self, handler: object) -> str | None:
        """Check MCP permission policy if handler is an MCP tool.

        Args:
            handler: Tool handler instance.

        Returns:
            Error message if permission denied, None if allowed or not MCP tool.
        """
        # Check if handler is an MCP tool adapter
        # We avoid direct isinstance check to prevent circular import
        # Instead check for MCP adapter attributes
        if not hasattr(handler, "server_name") or not hasattr(handler, "tool_name"):
            # Not an MCP tool, no MCP permission check needed
            return None

        server_name = getattr(handler, "server_name", None)
        tool_name = getattr(handler, "tool_name", None)

        if server_name is None or tool_name is None:
            # Invalid MCP handler, skip permission check
            return None

        # Apply MCP permission policy
        assert self._mcp_permission_policy is not None
        return self._mcp_permission_policy.authorize_tool(server_name, tool_name)


def _hitl_kind_from_context(context: ToolExecutionContext) -> str | None:
    """Infer HITL approval kind when a gated tool executes post-decision."""
    if context.approval_correlation_id is None:
        return None
    if context.session_id is not None:
        return "agent_tool"
    return "workflow_node"


def _normalize_handler_arguments(
    handler: object,
    arguments: dict[str, object],
) -> dict[str, object]:
    normalize = getattr(handler, "normalize_arguments", None)
    if not callable(normalize):
        return arguments
    normalized = normalize(arguments)
    if isinstance(normalized, dict):
        return normalized
    return arguments
