"""MCP server registry for process-wide server lifecycle management.

Stub for Phase 3 — Server Registry implementation.
"""

from __future__ import annotations

# TODO(phase-3): Implement McpServerRegistry
# - In-memory registry: server_name → McpClient instance + status
# - Server status: CONNECTING | CONNECTED | FAILED | DISCONNECTED
# - register(server_name, config) → connect → status tracking
# - unregister(server_name) → disconnect → cleanup
# - get(server_name) → active client or None
# - get_status(server_name) → server status
# - list_servers() → list of (server_name, status)
# - disconnect_all() → graceful shutdown for all servers
