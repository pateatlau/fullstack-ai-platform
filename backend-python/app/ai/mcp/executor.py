"""MCP tool execution adapter — implements ToolHandler for remote MCP tools.

Phase 4: Minimal stub for McpToolExecutionAdapter (full implementation in Phase 5).
Phase 5: Full Tool Execution Adapter implementation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.ai.mcp.client import McpClient
    from app.ai.tools.schemas import ToolExecutionContext, ToolResult


class McpToolExecutionAdapter:
    """Adapter implementing ToolHandler Protocol for remote MCP tool execution.

    Phase 4: Minimal constructor stub to support discovery.py instantiation.
    Phase 5: Full execute() implementation with MCP client.call_tool() delegation.
    """

    def __init__(
        self,
        server_name: str,
        tool_name: str,
        client: McpClient,
        metadata: dict[str, Any] | None = None,
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

    async def execute(
        self,
        args: dict[str, object],
        context: ToolExecutionContext,
    ) -> ToolResult:
        """Execute remote MCP tool call.

        Phase 5 TODO: Full implementation.
        - Call client.call_tool(tool_name, arguments)
        - Map MCP result → ToolResult(success=True, data=...)
        - Map MCP error → ToolResult(success=False, error=..., error_code="mcp_error")
        - Connection/transport errors → error_code="mcp_connection_error"
        - Timeout → error_code="timeout"
        - Structured log: server_name, tool_name, latency_ms, success (no raw data)

        Args:
            args: Tool arguments dict.
            context: Execution context (caller, request_id).

        Returns:
            ToolResult envelope.
        """
        # Phase 4: Stub raises NotImplementedError
        # Phase 5: Replace with full implementation
        raise NotImplementedError(
            "McpToolExecutionAdapter.execute() will be implemented in Phase 5"
        )
