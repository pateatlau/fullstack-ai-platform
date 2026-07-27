"""Stdio transport for MCP client — subprocess lifecycle + JSON-RPC stdin/stdout.

MCP specification 2024-11-05 stdio transport implementation.
Spawns MCP server subprocess, manages stdin/stdout JSON-RPC communication,
and handles graceful shutdown with timeout/force-terminate fallback.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from app.ai.mcp.auth import resolve_credential_env_vars
from app.ai.mcp.config import McpConnectionConfig
from app.ai.mcp.exceptions import McpConnectionError, McpToolExecutionError

logger = logging.getLogger(__name__)


class StdioTransport:
    """Low-level stdio transport for MCP JSON-RPC communication.

    Manages subprocess lifecycle (spawn, read/write, shutdown) and JSON-RPC
    protocol wrapper over stdin/stdout. Does not implement MCP client logic;
    use StdioMcpClient for the full McpClient Protocol interface.
    """

    def __init__(self, config: McpConnectionConfig) -> None:
        """Initialize stdio transport with connection config.

        Args:
            config: MCP server connection config (command, args, env, transport).
        """
        self.config = config
        self.process: asyncio.subprocess.Process | None = None
        self._request_id_counter = 0
        self._shutdown_timeout_seconds = 5.0

    async def connect(self, timeout_seconds: float = 10.0) -> None:
        """Spawn MCP server subprocess and establish stdin/stdout streams.

        Args:
            timeout_seconds: Max time to wait for subprocess spawn.

        Raises:
            McpConnectionError: On spawn failure, timeout, or subprocess crash.
        """
        if self.process is not None:
            raise McpConnectionError("Transport already connected")

        cmd = [self.config.command, *self.config.args]

        # Start with parent process env
        env_vars = dict(os.environ)

        # Merge config env vars (may contain placeholders)
        if self.config.env:
            resolved_config_env = resolve_credential_env_vars(
                dict(self.config.env), allow_missing=False
            )
            env_vars.update(resolved_config_env)

        # Merge credentials if provided
        if self.config.credentials:
            # Resolve credential env vars (interpolate ${VAR_NAME})
            if self.config.credentials.env_vars:
                resolved_cred_env = resolve_credential_env_vars(
                    dict(self.config.credentials.env_vars), allow_missing=False
                )
                env_vars.update(resolved_cred_env)

            # Merge api_keys into env (if needed by server)
            if self.config.credentials.api_keys:
                env_vars.update(dict(self.config.credentials.api_keys))

            # Append credential command args to command
            if self.config.credentials.command_args:
                cmd.extend(self.config.credentials.command_args)

        try:
            logger.info(
                "Spawning MCP server subprocess",
                extra={
                    "server_name": self.config.name,
                    "command": self.config.command,
                    "transport": self.config.transport,
                    "has_credentials": self.config.credentials is not None,
                },
            )

            self.process = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=env_vars,
                ),
                timeout=timeout_seconds,
            )

            logger.info(
                "MCP server subprocess spawned successfully",
                extra={
                    "server_name": self.config.name,
                    "pid": self.process.pid,
                },
            )

        except asyncio.TimeoutError as exc:
            raise McpConnectionError(
                f"MCP server spawn timeout after {timeout_seconds}s"
            ) from exc
        except OSError as exc:
            raise McpConnectionError(
                f"Failed to spawn MCP server subprocess: {exc}"
            ) from exc

    async def disconnect(self) -> None:
        """Gracefully shutdown subprocess with timeout/force-terminate fallback.

        Sends SIGTERM, waits for graceful exit, then SIGKILL after timeout.
        """
        if self.process is None:
            logger.debug("Transport not connected; skip disconnect")
            return

        logger.info(
            "Disconnecting MCP server",
            extra={"server_name": self.config.name, "pid": self.process.pid},
        )

        try:
            self.process.terminate()
            await asyncio.wait_for(
                self.process.wait(), timeout=self._shutdown_timeout_seconds
            )
            logger.info(
                "MCP server shutdown gracefully",
                extra={"server_name": self.config.name},
            )
        except asyncio.TimeoutError:
            logger.warning(
                "MCP server did not exit gracefully; force kill",
                extra={"server_name": self.config.name, "pid": self.process.pid},
            )
            if self.process.returncode is None:
                self.process.kill()
                await self.process.wait()
        except ProcessLookupError:
            logger.debug(
                "MCP server process already terminated",
                extra={"server_name": self.config.name},
            )
        finally:
            self.process = None

    async def send_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout_seconds: float = 30.0,
    ) -> dict[str, Any]:
        """Send JSON-RPC request and await response.

        Args:
            method: JSON-RPC method name (e.g. "tools/list", "tools/call").
            params: Optional method parameters dict.
            timeout_seconds: Max time to wait for response.

        Returns:
            JSON-RPC result dict.

        Raises:
            McpConnectionError: On transport error, subprocess crash, or timeout.
            McpToolExecutionError: On JSON-RPC error response or invalid format.
        """
        if (
            self.process is None
            or self.process.stdin is None
            or self.process.stdout is None
        ):
            raise McpConnectionError("Transport not connected")

        self._request_id_counter += 1
        request_id = self._request_id_counter

        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }

        try:
            request_json = json.dumps(request) + "\n"
            self.process.stdin.write(request_json.encode("utf-8"))
            await asyncio.wait_for(self.process.stdin.drain(), timeout=timeout_seconds)

            response_line = await asyncio.wait_for(
                self.process.stdout.readline(), timeout=timeout_seconds
            )

            if not response_line:
                raise McpConnectionError(f"MCP server closed stdout (method={method})")

            response = json.loads(response_line.decode("utf-8"))

            if response.get("id") != request_id:
                raise McpToolExecutionError(
                    f"JSON-RPC response id mismatch (expected={request_id}, got={response.get('id')})"
                )

            if "error" in response:
                error = response["error"]
                raise McpToolExecutionError(
                    f"MCP JSON-RPC error: {error.get('message', 'Unknown error')} "
                    f"(code={error.get('code', 'unknown')})"
                )

            if "result" not in response:
                raise McpToolExecutionError(
                    "MCP JSON-RPC response missing 'result' field"
                )

            return response["result"]

        except asyncio.TimeoutError as exc:
            raise McpConnectionError(
                f"MCP request timeout after {timeout_seconds}s (method={method})"
            ) from exc
        except json.JSONDecodeError as exc:
            raise McpToolExecutionError(
                f"Invalid JSON-RPC response from MCP server: {exc}"
            ) from exc
        except OSError as exc:
            raise McpConnectionError(
                f"MCP transport I/O error (method={method}): {exc}"
            ) from exc

    @property
    def is_connected(self) -> bool:
        """Check if subprocess is alive."""
        return self.process is not None and self.process.returncode is None


class StdioMcpClient:
    """MCP client implementation using stdio transport.

    Implements McpClient Protocol with subprocess-based JSON-RPC communication
    over stdin/stdout. Target spec: MCP specification 2024-11-05.
    """

    def __init__(
        self,
        config: McpConnectionConfig,
        connection_timeout: float = 10.0,
        tool_timeout: float = 30.0,
    ) -> None:
        """Initialize stdio MCP client.

        Args:
            config: MCP server connection config.
            connection_timeout: Timeout for connect/list_tools operations (default 10s).
            tool_timeout: Timeout for call_tool operations (default 30s).
        """
        self.config = config
        self.connection_timeout = connection_timeout
        self.tool_timeout = tool_timeout
        self._transport = StdioTransport(config)
        self._connected = False

    async def connect(self) -> None:
        """Establish connection to MCP server.

        Spawns subprocess and performs MCP handshake (initialize request).

        Raises:
            McpConnectionError: On connection failure, timeout, or handshake error.
        """
        if self._connected:
            raise McpConnectionError("Client already connected")

        await self._transport.connect(timeout_seconds=self.connection_timeout)

        try:
            result = await self._transport.send_request(
                method="initialize",
                params={
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "fullstack-ai-platform",
                        "version": "0.1.0",
                    },
                },
                timeout_seconds=self.connection_timeout,
            )

            logger.info(
                "MCP handshake completed",
                extra={
                    "server_name": self.config.name,
                    "protocol_version": result.get("protocolVersion"),
                },
            )

            self._connected = True

        except (McpConnectionError, McpToolExecutionError) as exc:
            await self._transport.disconnect()
            raise McpConnectionError(f"MCP handshake failed: {exc}") from exc

    async def disconnect(self) -> None:
        """Gracefully disconnect from MCP server.

        Sends shutdown signal, waits for process exit, then force-terminates
        (SIGTERM/SIGKILL) after timeout if needed.
        """
        if not self._connected:
            logger.debug("Client not connected; skip disconnect")
            return

        self._connected = False
        await self._transport.disconnect()

    async def list_tools(self) -> list[dict[str, Any]]:
        """Discover available tools from MCP server.

        Sends JSON-RPC tools/list request and parses response.

        Returns:
            List of MCP tool schemas (name, description, inputSchema).

        Raises:
            McpConnectionError: On transport error or timeout.
            McpToolExecutionError: On invalid or missing response.
        """
        if not self._connected:
            raise McpConnectionError("Client not connected")

        result = await self._transport.send_request(
            method="tools/list",
            params={},
            timeout_seconds=self.connection_timeout,
        )

        if not isinstance(result, dict) or "tools" not in result:
            raise McpToolExecutionError(
                "Invalid tools/list response: missing 'tools' field"
            )

        tools = result["tools"]
        if not isinstance(tools, list):
            raise McpToolExecutionError(
                "Invalid tools/list response: 'tools' is not a list"
            )

        logger.info(
            "Discovered MCP tools",
            extra={
                "server_name": self.config.name,
                "tool_count": len(tools),
            },
        )

        return tools

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
        if not self._connected:
            raise McpConnectionError("Client not connected")

        result = await self._transport.send_request(
            method="tools/call",
            params={"name": name, "arguments": arguments},
            timeout_seconds=self.tool_timeout,
        )

        if not isinstance(result, dict):
            raise McpToolExecutionError(
                f"Invalid tools/call response: expected dict, got {type(result)}"
            )

        logger.debug(
            "MCP tool call completed",
            extra={
                "server_name": self.config.name,
                "tool_name": name,
                "success": result.get("isError") is not True,
            },
        )

        return result

    @property
    def is_connected(self) -> bool:
        """Check if client is connected to MCP server."""
        return self._connected and self._transport.is_connected
