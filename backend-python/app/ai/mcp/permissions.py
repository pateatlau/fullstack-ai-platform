"""MCP permission policy — per-server/per-tool allowlists.

Phase 7: Permission Model implementation.

Provides per-server and per-tool authorization that composes with ToolAuthorizer.
Both policies must pass for MCP tool execution to proceed.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class McpPermissionPolicy:
    """Per-server/per-tool allowlist policy for MCP tool execution.

    Composes with ToolAuthorizer to enforce both authentication and MCP-specific
    permissions. Empty allowlists default to permissive (all allowed).

    Policy rules:
    - allowed_servers empty/absent → all configured servers allowed
    - allowed_tools absent → all tools from allowed servers allowed
    - allowed_tools[server] = ["*"] → all tools from server allowed
    - allowed_tools[server] = ["tool1", "tool2"] → only listed tools allowed
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialize MCP permission policy from config dict.

        Args:
            config: Permission policy config with optional keys:
                - allowed_servers: list[str] - server name allowlist
                - allowed_tools: dict[str, list[str]] - per-server tool allowlists
        """
        config = config or {}
        self.allowed_servers: list[str] = config.get("allowed_servers", [])
        self.allowed_tools: dict[str, list[str]] = config.get("allowed_tools", {})

        logger.debug(
            "MCP permission policy initialized",
            extra={
                "allowed_servers": self.allowed_servers,
                "allowed_tools_servers": list(self.allowed_tools.keys()),
            },
        )

    def authorize_server(self, server_name: str) -> str | None:
        """Check if server is allowed.

        Args:
            server_name: MCP server name to authorize.

        Returns:
            Error message if denied, None if allowed.
        """
        # Empty allowed_servers → all allowed
        if not self.allowed_servers:
            return None

        # Check if server is in allowlist
        if server_name not in self.allowed_servers:
            logger.warning(
                "MCP server denied by permission policy",
                extra={
                    "server_name": server_name,
                    "allowed_servers": self.allowed_servers,
                    "mcp_permission_denied": True,
                },
            )
            return f"MCP server '{server_name}' is not in the allowed servers list"

        return None

    def authorize_tool(self, server_name: str, tool_name: str) -> str | None:
        """Check if tool from server is allowed.

        Args:
            server_name: MCP server name.
            tool_name: Original (unprefixed) tool name.

        Returns:
            Error message if denied, None if allowed.
        """
        # First check server authorization
        server_auth_error = self.authorize_server(server_name)
        if server_auth_error is not None:
            return server_auth_error

        # Empty allowed_tools → all tools from allowed servers allowed
        if not self.allowed_tools:
            return None

        # No entry for this server → all tools from server allowed
        if server_name not in self.allowed_tools:
            return None

        # Check tool allowlist for server
        allowed_tool_list = self.allowed_tools[server_name]

        # Wildcard "*" → all tools from server allowed
        if "*" in allowed_tool_list:
            return None

        # Check if specific tool is in allowlist
        if tool_name not in allowed_tool_list:
            logger.warning(
                "MCP tool denied by permission policy",
                extra={
                    "server_name": server_name,
                    "tool_name": tool_name,
                    "allowed_tools": allowed_tool_list,
                    "mcp_permission_denied": True,
                },
            )
            return (
                f"MCP tool '{tool_name}' from server '{server_name}' "
                f"is not in the allowed tools list"
            )

        return None
