"""MCP client Protocol and abstract interface for MCP JSON-RPC operations."""

from __future__ import annotations

from typing import Any, Protocol


class McpClient(Protocol):
    """Abstract MCP client interface for JSON-RPC operations.

    Concrete implementations (e.g. StdioMcpClient) handle transport-specific
    details (subprocess lifecycle, stdin/stdout, SSE, etc.) and expose this
    uniform async API.

    Target spec: MCP specification 2024-11-05 (stdio transport primary).
    """

    async def connect(self) -> None:
        """Establish connection to MCP server.

        For stdio transport: spawns subprocess, performs MCP handshake (e.g.
        initialize request if spec requires).

        Raises:
            McpConnectionError: On connection failure, timeout, or handshake error.
        """
        ...

    async def disconnect(self) -> None:
        """Gracefully disconnect from MCP server.

        For stdio transport: sends shutdown signal, waits for process exit,
        then force-terminates (SIGTERM/SIGKILL) after timeout if needed.
        """
        ...

    async def list_tools(self) -> list[dict[str, Any]]:
        """Discover available tools from MCP server.

        Sends JSON-RPC tools/list request and parses response.

        Returns:
            List of MCP tool schemas (name, description, inputSchema).

        Raises:
            McpConnectionError: On transport error or timeout.
            McpDiscoveryError: On invalid or missing response.
        """
        ...

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute remote MCP tool call.

        Sends JSON-RPC tools/call request with tool name and arguments; parses
        result or error response.

        Args:
            name: MCP tool name (not prefixed; original server-side name).
            arguments: Tool arguments dict (validated against inputSchema).

        Returns:
            MCP tool result dict (success/error envelope).

        Raises:
            McpConnectionError: On transport error or timeout.
            McpToolExecutionError: On invalid response or remote error.
        """
        ...
