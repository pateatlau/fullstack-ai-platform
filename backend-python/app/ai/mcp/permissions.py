"""MCP permission policy — per-server/per-tool allowlists.

Stub for Phase 7 — Permission Model implementation.
"""

from __future__ import annotations

# TODO(phase-7): Implement McpPermissionPolicy
# - Config: allowed_servers list, allowed_tools dict
# - authorize_server(server_name) → str | None (error message or None)
# - authorize_tool(server_name, tool_name) → str | None (check wildcard "*")
# - Compose with ToolAuthorizer: both must pass (authenticated-only inherited)
# - Extend app/core/config.py: mcp_permission_policy dict field
# - Empty allowed_servers → all configured servers allowed
# - Empty allowed_tools → all tools from allowed servers allowed
# - allowed_tools[server] = ["*"] → all tools from server allowed
