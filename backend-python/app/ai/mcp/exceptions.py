"""MCP-specific exceptions for connection, discovery, and execution errors."""

from __future__ import annotations


class McpConnectionError(Exception):
    """Raised when MCP server connection or transport fails."""

    pass


class McpToolExecutionError(Exception):
    """Raised when MCP tool execution fails remotely or response is invalid."""

    pass


class McpDiscoveryError(Exception):
    """Raised when MCP tool discovery or capability validation fails."""

    pass


class McpAuthenticationError(Exception):
    """Raised when MCP server authentication or credential resolution fails."""

    pass


class McpPermissionDeniedError(Exception):
    """Raised when MCP permission policy denies server or tool access."""

    pass
