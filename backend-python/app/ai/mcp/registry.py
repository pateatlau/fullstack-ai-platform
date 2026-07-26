"""MCP server registry for process-wide server lifecycle management.

Process-scoped singleton registry tracking MCP server connections and health
status. Manages server lifecycle (register/unregister/connect/disconnect) and
provides diagnostics via status tracking.
"""

from __future__ import annotations

import asyncio
import logging
from enum import Enum
from typing import TYPE_CHECKING

from app.ai.mcp.config import McpConnectionConfig
from app.ai.mcp.exceptions import McpConnectionError
from app.ai.mcp.transport.stdio import StdioMcpClient

if TYPE_CHECKING:
    from app.ai.mcp.client import McpClient

logger = logging.getLogger(__name__)


class ServerStatus(str, Enum):
    """MCP server connection status for health tracking."""

    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    FAILED = "FAILED"
    DISCONNECTED = "DISCONNECTED"


class McpServerRegistry:
    """Process-scoped MCP server registry with connection lifecycle management.

    Tracks active MCP server connections and their health status. Provides
    registration/unregistration APIs and diagnostic queries for observability.

    Thread-safe for async operations (asyncio single-threaded assumption).
    """

    def __init__(
        self,
        connection_timeout: float = 10.0,
        tool_timeout: float = 30.0,
    ) -> None:
        """Initialize empty server registry.

        Args:
            connection_timeout: Default timeout for server connect/list operations.
            tool_timeout: Default timeout for tool execution.
        """
        self._clients: dict[str, McpClient] = {}
        self._statuses: dict[str, ServerStatus] = {}
        self._connection_timeout = connection_timeout
        self._tool_timeout = tool_timeout

    async def register(self, server_name: str, config: McpConnectionConfig) -> None:
        """Register and connect to MCP server.

        Sets status to CONNECTING, instantiates client, attempts connection,
        updates status to CONNECTED on success or FAILED on error.

        Args:
            server_name: Unique server identifier (must match config.name).
            config: MCP server connection config (command, args, env, transport).

        Raises:
            ValueError: If server_name already registered or config.name mismatch.
            McpConnectionError: If connection fails (status set to FAILED).
        """
        if server_name in self._clients:
            raise ValueError(f"MCP server '{server_name}' already registered")

        current_status = self._statuses.get(server_name)
        if current_status == ServerStatus.CONNECTING:
            raise ValueError(
                f"MCP server '{server_name}' registration already in progress"
            )

        if config.name != server_name:
            raise ValueError(
                f"Server name mismatch: registry key '{server_name}' != config.name '{config.name}'"
            )

        self._statuses[server_name] = ServerStatus.CONNECTING

        logger.info(
            "Registering MCP server",
            extra={"server_name": server_name, "status": ServerStatus.CONNECTING},
        )

        try:
            client = StdioMcpClient(
                config=config,
                connection_timeout=self._connection_timeout,
                tool_timeout=self._tool_timeout,
            )

            await client.connect()

            self._clients[server_name] = client
            self._statuses[server_name] = ServerStatus.CONNECTED

            logger.info(
                "MCP server registered successfully",
                extra={"server_name": server_name, "status": ServerStatus.CONNECTED},
            )

        except (McpConnectionError, Exception) as exc:
            self._statuses[server_name] = ServerStatus.FAILED

            logger.error(
                "MCP server registration failed",
                extra={
                    "server_name": server_name,
                    "status": ServerStatus.FAILED,
                    "error": str(exc),
                },
            )

            raise McpConnectionError(
                f"Failed to register MCP server '{server_name}': {exc}"
            ) from exc

    async def unregister(self, server_name: str) -> None:
        """Unregister MCP server and disconnect client.

        Calls disconnect on client, sets status to DISCONNECTED, removes from
        registry. No-op if server not registered (idempotent).

        Args:
            server_name: Server identifier to unregister.
        """
        if server_name not in self._clients:
            logger.debug(
                "MCP server not registered; skip unregister",
                extra={"server_name": server_name},
            )
            return

        logger.info(
            "Unregistering MCP server",
            extra={"server_name": server_name},
        )

        client = self._clients.pop(server_name)

        try:
            await client.disconnect()
        except Exception as exc:
            logger.warning(
                "Error during MCP server disconnect",
                extra={"server_name": server_name, "error": str(exc)},
            )

        self._statuses[server_name] = ServerStatus.DISCONNECTED

        logger.info(
            "MCP server unregistered",
            extra={"server_name": server_name, "status": ServerStatus.DISCONNECTED},
        )

    def get(self, server_name: str) -> McpClient | None:
        """Get active MCP client by server name.

        Args:
            server_name: Server identifier.

        Returns:
            Active McpClient instance or None if not registered.
        """
        return self._clients.get(server_name)

    def get_status(self, server_name: str) -> ServerStatus | None:
        """Get MCP server status by server name.

        Args:
            server_name: Server identifier.

        Returns:
            Current ServerStatus or None if never registered.
        """
        return self._statuses.get(server_name)

    def list_servers(self) -> list[tuple[str, ServerStatus]]:
        """List all registered MCP servers with current status.

        Returns:
            List of (server_name, status) tuples. Only includes servers
            currently in the registry (excludes DISCONNECTED unless still tracked).
        """
        return [
            (server_name, self._statuses.get(server_name, ServerStatus.DISCONNECTED))
            for server_name in self._clients.keys()
        ]

    async def disconnect_all(self) -> None:
        """Gracefully disconnect all registered MCP servers.

        Called during app shutdown. Disconnects all servers in parallel,
        then updates statuses to DISCONNECTED. Errors logged but not raised.
        """
        if not self._clients:
            logger.debug("No MCP servers to disconnect")
            return

        logger.info(
            "Disconnecting all MCP servers",
            extra={"server_count": len(self._clients)},
        )

        disconnect_tasks = [
            self._disconnect_one(server_name, client)
            for server_name, client in self._clients.items()
        ]

        await asyncio.gather(*disconnect_tasks, return_exceptions=True)

        self._clients.clear()

        for server_name in list(self._statuses.keys()):
            if self._statuses[server_name] in (
                ServerStatus.CONNECTED,
                ServerStatus.CONNECTING,
            ):
                self._statuses[server_name] = ServerStatus.DISCONNECTED

        logger.info("All MCP servers disconnected")

    async def _disconnect_one(self, server_name: str, client: McpClient) -> None:
        """Disconnect a single MCP server with error handling.

        Args:
            server_name: Server identifier (for logging).
            client: McpClient instance to disconnect.
        """
        try:
            await client.disconnect()
            logger.debug(
                "MCP server disconnected",
                extra={"server_name": server_name},
            )
        except Exception as exc:
            logger.warning(
                "Error disconnecting MCP server",
                extra={"server_name": server_name, "error": str(exc)},
            )
