"""Orchestrate the tool lifecycle: registry → validation → auth → execution."""

from __future__ import annotations

import asyncio
import time

from app.ai.tools.authorizer import ToolAuthorizer
from app.ai.tools.registry import ToolRegistry
from app.ai.tools.schemas import ToolCall, ToolExecutionContext, ToolResult
from app.ai.tools.validator import ToolValidator
from app.core.config import Settings
from app.core.logging import get_logger

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
    ) -> None:
        self._registry = registry
        self._settings = settings
        self._validator = validator or ToolValidator()
        self._authorizer = authorizer or ToolAuthorizer()

    async def execute(
        self,
        call: ToolCall,
        context: ToolExecutionContext,
    ) -> ToolResult:
        start = time.perf_counter()
        tool_name = call.name

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
            )

        auth_error = self._authorizer.authorize(tool, context)
        if auth_error is not None:
            return self._finalize(
                call=call,
                context=context,
                result=ToolResult(
                    success=False,
                    error=auth_error,
                    error_code="forbidden",
                ),
                start=start,
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
            )

        if not handler_result.success:
            return self._finalize(
                call=call,
                context=context,
                result=handler_result,
                start=start,
            )

        metadata = dict(handler_result.metadata)
        metadata.setdefault("tool_name", tool_name)
        return self._finalize(
            call=call,
            context=context,
            result=handler_result.model_copy(update={"metadata": metadata}),
            start=start,
        )

    def _finalize(
        self,
        *,
        call: ToolCall,
        context: ToolExecutionContext,
        result: ToolResult,
        start: float,
    ) -> ToolResult:
        latency_ms = int((time.perf_counter() - start) * 1000)
        tool_name = call.name

        metadata = dict(result.metadata)
        metadata["tool_name"] = tool_name
        metadata["latency_ms"] = latency_ms
        if call.call_id is not None:
            metadata["call_id"] = call.call_id

        normalized = result.model_copy(update={"metadata": metadata})

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
