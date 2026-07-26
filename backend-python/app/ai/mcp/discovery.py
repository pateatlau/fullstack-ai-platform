"""MCP tool discovery — map MCP tools/list to ToolDefinition.

Stub for Phase 4 — Tool Discovery implementation.
"""

from __future__ import annotations

# TODO(phase-4): Implement McpToolDiscovery
# - discover(client, server_name) → list[(ToolDefinition, McpToolExecutionAdapter)]
# - Validate server capabilities: tools/list, tools/call required
# - Prefix tool names: {server_name}.{tool_name} (collision prevention)
# - Map MCP inputSchema → ToolDefinition.parameters (JSON Schema)
# - Preserve tool origin metadata: source="mcp", server_name, transport, original_name
# - Instantiate McpToolExecutionAdapter per tool
