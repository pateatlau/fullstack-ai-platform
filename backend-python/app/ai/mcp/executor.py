"""MCP tool execution adapter — implements ToolHandler for remote MCP tools.

Phase 5: Full Tool Execution Adapter implementation.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from app.ai.mcp.exceptions import (
    McpConnectionError,
    McpToolExecutionError,
)
from app.ai.tools.schemas import ToolResult
from app.core.config import Settings, get_settings
from app.middleware.rate_limit import check_rate_limit_bucket

if TYPE_CHECKING:
    from app.ai.mcp.client import McpClient
    from app.ai.tools.schemas import ToolExecutionContext

logger = logging.getLogger(__name__)


class McpToolExecutionAdapter:
    """Adapter implementing ToolHandler Protocol for remote MCP tool execution.

    Delegates to McpClient.call_tool() and maps MCP results to ToolResult envelope.
    Handles connection errors, timeouts, and remote execution failures gracefully.
    """

    def __init__(
        self,
        server_name: str,
        tool_name: str,
        client: McpClient,
        metadata: dict[str, Any] | None = None,
        settings: Settings | None = None,
    ) -> None:
        """Initialize MCP tool execution adapter.

        Args:
            server_name: MCP server name.
            tool_name: Original (unprefixed) MCP tool name.
            client: Connected MCP client instance.
            metadata: Tool origin metadata (source, server_name, transport, etc.).
        """
        self.server_name = server_name
        self.tool_name = tool_name
        self.client = client
        self.metadata = metadata or {}
        self.settings = settings or get_settings()

    async def execute(
        self,
        args: dict[str, object],
        context: ToolExecutionContext,
    ) -> ToolResult:
        """Execute remote MCP tool call.

        Calls client.call_tool() and maps MCP results to ToolResult:
        - MCP success → ToolResult(success=True, data=...)
        - MCP error → ToolResult(success=False, error=..., error_code="mcp_error")
        - Connection/transport errors → error_code="mcp_connection_error"
        - Timeout → error_code="timeout"

        Structured logging includes: server_name, tool_name, latency_ms, success.
        Raw arguments and responses are NOT logged.

        Args:
            args: Tool arguments dict.
            context: Execution context (caller, request_id).

        Returns:
            ToolResult envelope.
        """
        start_time = time.perf_counter()
        success = False
        error_code: str | None = None

        try:
            if (
                self.settings.security_rate_limit_extensions_enabled
                and context.caller.user_id
            ):
                from app.ai.security.quotas.store import check_daily_usage_quota

                daily_allowed = await check_daily_usage_quota(
                    str(context.caller.user_id),
                    "mcp",
                    self.settings.mcp_invocation_daily_quota,
                )
                if not daily_allowed:
                    return ToolResult(
                        success=False,
                        error="Daily MCP invocation quota exceeded",
                        error_code="rate_limit_exceeded",
                        metadata={
                            "server_name": self.server_name,
                            "tool_name": self.tool_name,
                            "source": "mcp",
                        },
                    )
                retry_after = await check_rate_limit_bucket(
                    f"mcp:{context.caller.user_id}",
                    self.settings.mcp_invocation_per_minute,
                )
                if retry_after is not None:
                    return ToolResult(
                        success=False,
                        error="MCP invocation rate limit exceeded",
                        error_code="rate_limit_exceeded",
                        metadata={
                            "server_name": self.server_name,
                            "tool_name": self.tool_name,
                            "source": "mcp",
                            "retry_after": retry_after,
                        },
                    )
            mcp_result = await self.client.call_tool(self.tool_name, args)

            success = True
            latency_ms = int((time.perf_counter() - start_time) * 1000)

            logger.info(
                "MCP tool execution succeeded",
                extra={
                    "server_name": self.server_name,
                    "tool_name": self.tool_name,
                    "tool_source": "mcp",
                    "latency_ms": latency_ms,
                    "success": success,
                    "request_id": context.request_id,
                },
            )

            return ToolResult(
                success=True,
                data=mcp_result,
                metadata={
                    "server_name": self.server_name,
                    "tool_name": self.tool_name,
                    "source": "mcp",
                },
            )

        except asyncio.TimeoutError as exc:
            error_code = "timeout"
            latency_ms = int((time.perf_counter() - start_time) * 1000)

            logger.warning(
                "MCP tool execution timeout",
                extra={
                    "server_name": self.server_name,
                    "tool_name": self.tool_name,
                    "tool_source": "mcp",
                    "latency_ms": latency_ms,
                    "success": success,
                    "error_code": error_code,
                    "request_id": context.request_id,
                },
            )

            return ToolResult(
                success=False,
                error=f"MCP tool call timed out: {exc}",
                error_code=error_code,
                metadata={
                    "server_name": self.server_name,
                    "tool_name": self.tool_name,
                    "source": "mcp",
                },
            )

        except McpConnectionError as exc:
            error_code = "mcp_connection_error"
            latency_ms = int((time.perf_counter() - start_time) * 1000)

            logger.error(
                "MCP tool execution failed: connection error",
                extra={
                    "server_name": self.server_name,
                    "tool_name": self.tool_name,
                    "tool_source": "mcp",
                    "latency_ms": latency_ms,
                    "success": success,
                    "error_code": error_code,
                    "request_id": context.request_id,
                },
                exc_info=True,
            )

            return ToolResult(
                success=False,
                error=f"MCP connection error: {exc}",
                error_code=error_code,
                metadata={
                    "server_name": self.server_name,
                    "tool_name": self.tool_name,
                    "source": "mcp",
                },
            )

        except McpToolExecutionError as exc:
            error_code = "mcp_error"
            latency_ms = int((time.perf_counter() - start_time) * 1000)

            logger.error(
                "MCP tool execution failed: remote error",
                extra={
                    "server_name": self.server_name,
                    "tool_name": self.tool_name,
                    "tool_source": "mcp",
                    "latency_ms": latency_ms,
                    "success": success,
                    "error_code": error_code,
                    "request_id": context.request_id,
                },
                exc_info=True,
            )

            return ToolResult(
                success=False,
                error=f"MCP tool execution error: {exc}",
                error_code=error_code,
                metadata={
                    "server_name": self.server_name,
                    "tool_name": self.tool_name,
                    "source": "mcp",
                },
            )

        except Exception as exc:
            error_code = "unknown_error"
            latency_ms = int((time.perf_counter() - start_time) * 1000)

            logger.error(
                "MCP tool execution failed: unexpected error",
                extra={
                    "server_name": self.server_name,
                    "tool_name": self.tool_name,
                    "tool_source": "mcp",
                    "latency_ms": latency_ms,
                    "success": success,
                    "error_code": error_code,
                    "request_id": context.request_id,
                },
                exc_info=True,
            )

            return ToolResult(
                success=False,
                error=f"Unexpected error during MCP tool execution: {exc}",
                error_code=error_code,
                metadata={
                    "server_name": self.server_name,
                    "tool_name": self.tool_name,
                    "source": "mcp",
                },
            )
