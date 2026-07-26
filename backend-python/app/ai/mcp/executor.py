"""MCP tool execution adapter — implements ToolHandler for remote MCP tools.

Stub for Phase 5 — Tool Execution Adapter implementation.
"""

from __future__ import annotations

# TODO(phase-5): Implement McpToolExecutionAdapter (implements ToolHandler)
# - __init__(server_name, tool_name, client: McpClient)
# - execute(arguments, context) → ToolResult
# - Call client.call_tool(tool_name, arguments)
# - Map MCP result → ToolResult(success=True, data=...)
# - Map MCP error → ToolResult(success=False, error=..., error_code="mcp_error")
# - Connection/transport errors → error_code="mcp_connection_error"
# - Timeout → error_code="timeout"
# - Structured log: server_name, tool_name, latency_ms, success (no raw data)
