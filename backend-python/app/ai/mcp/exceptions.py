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

    def __init__(self, message: str, *, missing_key: str | None = None) -> None:
        super().__init__(message)
        # Key name only — never an attempted/resolved value (audit-safe).
        self.missing_key = missing_key


class McpPermissionDeniedError(Exception):
    """Raised when MCP permission policy denies server or tool access."""

    pass
