"""Tests for stdio MCP client — transport and client with mocked subprocess."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.ai.mcp.config import McpConnectionConfig
from app.ai.mcp.exceptions import McpConnectionError, McpToolExecutionError
from app.ai.mcp.transport.stdio import StdioMcpClient, StdioTransport

pytestmark = pytest.mark.anyio


@pytest.fixture
def mock_config() -> McpConnectionConfig:
    """Create a test MCP connection config."""
    return McpConnectionConfig(
        name="test-server",
        command="test-mcp-server",
        args=["--arg1", "value1"],
        env={"TEST_VAR": "test_value"},
        transport="stdio",
    )


@pytest.fixture
def mock_process() -> Mock:
    """Create a mock subprocess.Process with stdin/stdout/stderr."""
    process = Mock()
    process.pid = 12345
    process.returncode = None

    process.stdin = AsyncMock()
    process.stdin.write = Mock()
    process.stdin.drain = AsyncMock()

    process.stdout = AsyncMock()
    process.stdout.readline = AsyncMock()

    process.stderr = AsyncMock()

    process.wait = AsyncMock()
    process.terminate = Mock()
    process.kill = Mock()

    return process


class TestStdioTransport:
    """Test StdioTransport subprocess lifecycle and JSON-RPC communication."""

    async def test_connect_success(
        self, mock_config: McpConnectionConfig, mock_process: Mock
    ) -> None:
        """Test successful subprocess spawn and connection."""
        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            transport = StdioTransport(mock_config)
            await transport.connect(timeout_seconds=10.0)

            assert transport.process is mock_process
            assert transport.is_connected is True

    async def test_connect_timeout(self, mock_config: McpConnectionConfig) -> None:
        """Test connection timeout during subprocess spawn."""

        async def slow_spawn(*args: Any, **kwargs: Any) -> None:
            await asyncio.sleep(100)

        with patch("asyncio.create_subprocess_exec", side_effect=slow_spawn):
            transport = StdioTransport(mock_config)

            with pytest.raises(McpConnectionError, match="timeout"):
                await transport.connect(timeout_seconds=0.1)

    async def test_connect_spawn_failure(
        self, mock_config: McpConnectionConfig
    ) -> None:
        """Test subprocess spawn failure (command not found)."""
        with patch(
            "asyncio.create_subprocess_exec", side_effect=OSError("Command not found")
        ):
            transport = StdioTransport(mock_config)

            with pytest.raises(McpConnectionError, match="Failed to spawn"):
                await transport.connect()

    async def test_connect_missing_secret_is_audited(self) -> None:
        """A missing credential env var audit-logs the key name, then re-raises."""
        from app.ai.deps import get_audit_logger
        from app.ai.mcp.exceptions import McpAuthenticationError
        from app.ai.security.audit.actions import AuditAction
        from app.ai.security.audit.logger import AuditLogger
        from app.ai.security.audit.models import AuditEvent, AuditOutcome
        from app.core.config import Settings

        class FakeAuditStore:
            def __init__(self) -> None:
                self.events: list[AuditEvent] = []

            async def insert(self, event: AuditEvent) -> None:
                self.events.append(event)

            async def query(self, **_: object) -> list[AuditEvent]:
                return list(self.events)

        fake_store = FakeAuditStore()
        fake_logger = AuditLogger(
            fake_store,
            settings=Settings(
                security_governance_enabled=True, security_audit_log_enabled=True
            ),
        )
        get_audit_logger.cache_clear()

        config = McpConnectionConfig(
            name="test-server",
            command="test-mcp-server",
            env={"TOKEN": "${MISSING_CREDENTIAL_VAR}"},
            transport="stdio",
        )
        transport = StdioTransport(config)

        with patch("app.ai.deps.get_audit_logger", return_value=fake_logger):
            with pytest.raises(McpAuthenticationError):
                await transport.connect()

        get_audit_logger.cache_clear()
        assert len(fake_store.events) == 1
        event = fake_store.events[0]
        assert event.action == AuditAction.SECRET_RESOLUTION_MISSING
        assert event.outcome is AuditOutcome.ERROR
        assert event.resource_id == "MISSING_CREDENTIAL_VAR"

    async def test_connect_already_connected(
        self, mock_config: McpConnectionConfig, mock_process: Mock
    ) -> None:
        """Test error when connecting twice."""
        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            transport = StdioTransport(mock_config)
            await transport.connect()

            with pytest.raises(McpConnectionError, match="already connected"):
                await transport.connect()

    async def test_disconnect_graceful(
        self, mock_config: McpConnectionConfig, mock_process: Mock
    ) -> None:
        """Test graceful subprocess shutdown."""
        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            transport = StdioTransport(mock_config)
            await transport.connect()

            mock_process.returncode = 0
            await transport.disconnect()

            mock_process.terminate.assert_called_once()
            mock_process.wait.assert_awaited_once()
            mock_process.kill.assert_not_called()
            assert transport.process is None

    async def test_disconnect_force_kill(
        self, mock_config: McpConnectionConfig, mock_process: Mock
    ) -> None:
        """Test force kill after graceful shutdown timeout."""

        async def slow_wait() -> None:
            await asyncio.sleep(100)

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            transport = StdioTransport(mock_config)
            transport._shutdown_timeout_seconds = 0.1
            await transport.connect()

            mock_process.wait.side_effect = slow_wait
            await transport.disconnect()

            mock_process.terminate.assert_called_once()
            mock_process.kill.assert_called_once()
            assert transport.process is None

    async def test_disconnect_not_connected(
        self, mock_config: McpConnectionConfig
    ) -> None:
        """Test disconnect when not connected (no-op)."""
        transport = StdioTransport(mock_config)
        await transport.disconnect()
        assert transport.process is None

    async def test_send_request_success(
        self, mock_config: McpConnectionConfig, mock_process: Mock
    ) -> None:
        """Test successful JSON-RPC request/response."""
        response = {"jsonrpc": "2.0", "id": 1, "result": {"tools": []}}
        response_bytes = (json.dumps(response) + "\n").encode("utf-8")

        mock_process.stdout.readline.return_value = response_bytes

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            transport = StdioTransport(mock_config)
            await transport.connect()

            result = await transport.send_request(
                "tools/list", {}, timeout_seconds=10.0
            )

            assert result == {"tools": []}
            mock_process.stdin.write.assert_called_once()
            mock_process.stdin.drain.assert_awaited_once()
            mock_process.stdout.readline.assert_awaited_once()

    async def test_send_request_not_connected(
        self, mock_config: McpConnectionConfig
    ) -> None:
        """Test send_request when not connected."""
        transport = StdioTransport(mock_config)

        with pytest.raises(McpConnectionError, match="not connected"):
            await transport.send_request("tools/list")

    async def test_send_request_timeout(
        self, mock_config: McpConnectionConfig, mock_process: Mock
    ) -> None:
        """Test request timeout."""

        async def slow_readline() -> bytes:
            await asyncio.sleep(100)
            return b""

        mock_process.stdout.readline.side_effect = slow_readline

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            transport = StdioTransport(mock_config)
            await transport.connect()

            with pytest.raises(McpConnectionError, match="timeout"):
                await transport.send_request("tools/list", timeout_seconds=0.1)

    async def test_send_request_stdout_closed(
        self, mock_config: McpConnectionConfig, mock_process: Mock
    ) -> None:
        """Test subprocess closed stdout (crashed)."""
        mock_process.stdout.readline.return_value = b""

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            transport = StdioTransport(mock_config)
            await transport.connect()

            with pytest.raises(McpConnectionError, match="closed stdout"):
                await transport.send_request("tools/list")

    async def test_send_request_invalid_json(
        self, mock_config: McpConnectionConfig, mock_process: Mock
    ) -> None:
        """Test invalid JSON response."""
        mock_process.stdout.readline.return_value = b"not-valid-json\n"

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            transport = StdioTransport(mock_config)
            await transport.connect()

            with pytest.raises(McpToolExecutionError, match="Invalid JSON-RPC"):
                await transport.send_request("tools/list")

    async def test_send_request_id_mismatch(
        self, mock_config: McpConnectionConfig, mock_process: Mock
    ) -> None:
        """Test JSON-RPC response id mismatch."""
        response = {"jsonrpc": "2.0", "id": 999, "result": {}}
        response_bytes = (json.dumps(response) + "\n").encode("utf-8")
        mock_process.stdout.readline.return_value = response_bytes

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            transport = StdioTransport(mock_config)
            await transport.connect()

            with pytest.raises(McpToolExecutionError, match="id mismatch"):
                await transport.send_request("tools/list")

    async def test_send_request_json_rpc_error(
        self, mock_config: McpConnectionConfig, mock_process: Mock
    ) -> None:
        """Test JSON-RPC error response from server."""
        response = {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32601, "message": "Method not found"},
        }
        response_bytes = (json.dumps(response) + "\n").encode("utf-8")
        mock_process.stdout.readline.return_value = response_bytes

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            transport = StdioTransport(mock_config)
            await transport.connect()

            with pytest.raises(McpToolExecutionError, match="Method not found"):
                await transport.send_request("invalid/method")

    async def test_send_request_missing_result(
        self, mock_config: McpConnectionConfig, mock_process: Mock
    ) -> None:
        """Test JSON-RPC response missing result field."""
        response = {"jsonrpc": "2.0", "id": 1}
        response_bytes = (json.dumps(response) + "\n").encode("utf-8")
        mock_process.stdout.readline.return_value = response_bytes

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            transport = StdioTransport(mock_config)
            await transport.connect()

            with pytest.raises(McpToolExecutionError, match="missing 'result'"):
                await transport.send_request("tools/list")


class TestStdioMcpClient:
    """Test StdioMcpClient MCP Protocol implementation."""

    async def test_connect_success(
        self, mock_config: McpConnectionConfig, mock_process: Mock
    ) -> None:
        """Test successful MCP client connection with handshake."""
        init_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"list": {}, "call": {}}},
                "serverInfo": {"name": "test-server", "version": "1.0.0"},
            },
        }
        response_bytes = (json.dumps(init_response) + "\n").encode("utf-8")
        mock_process.stdout.readline.return_value = response_bytes

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            client = StdioMcpClient(
                mock_config, connection_timeout=10.0, tool_timeout=30.0
            )
            await client.connect()

            assert client.is_connected is True
            mock_process.stdin.write.assert_called_once()

    async def test_connect_already_connected(
        self, mock_config: McpConnectionConfig, mock_process: Mock
    ) -> None:
        """Test error when connecting twice."""
        init_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"protocolVersion": "2024-11-05", "capabilities": {}},
        }
        response_bytes = (json.dumps(init_response) + "\n").encode("utf-8")
        mock_process.stdout.readline.return_value = response_bytes

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            client = StdioMcpClient(mock_config)
            await client.connect()

            with pytest.raises(McpConnectionError, match="already connected"):
                await client.connect()

    async def test_connect_handshake_failure(
        self, mock_config: McpConnectionConfig, mock_process: Mock
    ) -> None:
        """Test connection failure during handshake."""
        error_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32600, "message": "Invalid request"},
        }
        response_bytes = (json.dumps(error_response) + "\n").encode("utf-8")
        mock_process.stdout.readline.return_value = response_bytes

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            client = StdioMcpClient(mock_config)

            with pytest.raises(McpConnectionError, match="handshake failed"):
                await client.connect()

            assert client.is_connected is False

    async def test_disconnect_success(
        self, mock_config: McpConnectionConfig, mock_process: Mock
    ) -> None:
        """Test graceful disconnect."""
        init_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"protocolVersion": "2024-11-05", "capabilities": {}},
        }
        response_bytes = (json.dumps(init_response) + "\n").encode("utf-8")
        mock_process.stdout.readline.return_value = response_bytes
        mock_process.returncode = 0

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            client = StdioMcpClient(mock_config)
            await client.connect()
            await client.disconnect()

            assert client.is_connected is False
            mock_process.terminate.assert_called_once()

    async def test_disconnect_not_connected(
        self, mock_config: McpConnectionConfig
    ) -> None:
        """Test disconnect when not connected (no-op)."""
        client = StdioMcpClient(mock_config)
        await client.disconnect()
        assert client.is_connected is False

    async def test_list_tools_success(
        self, mock_config: McpConnectionConfig, mock_process: Mock
    ) -> None:
        """Test successful tool discovery."""
        init_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"protocolVersion": "2024-11-05", "capabilities": {}},
        }
        list_response = {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "tools": [
                    {
                        "name": "read_file",
                        "description": "Read a file",
                        "inputSchema": {"type": "object", "properties": {}},
                    },
                    {
                        "name": "list_directory",
                        "description": "List directory",
                        "inputSchema": {"type": "object", "properties": {}},
                    },
                ]
            },
        }

        responses = [
            (json.dumps(init_response) + "\n").encode("utf-8"),
            (json.dumps(list_response) + "\n").encode("utf-8"),
        ]
        mock_process.stdout.readline.side_effect = responses

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            client = StdioMcpClient(mock_config)
            await client.connect()
            tools = await client.list_tools()

            assert len(tools) == 2
            assert tools[0]["name"] == "read_file"
            assert tools[1]["name"] == "list_directory"

    async def test_list_tools_not_connected(
        self, mock_config: McpConnectionConfig
    ) -> None:
        """Test list_tools when not connected."""
        client = StdioMcpClient(mock_config)

        with pytest.raises(McpConnectionError, match="not connected"):
            await client.list_tools()

    async def test_list_tools_invalid_response(
        self, mock_config: McpConnectionConfig, mock_process: Mock
    ) -> None:
        """Test list_tools with invalid response format."""
        init_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"protocolVersion": "2024-11-05", "capabilities": {}},
        }
        list_response = {"jsonrpc": "2.0", "id": 2, "result": {}}

        responses = [
            (json.dumps(init_response) + "\n").encode("utf-8"),
            (json.dumps(list_response) + "\n").encode("utf-8"),
        ]
        mock_process.stdout.readline.side_effect = responses

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            client = StdioMcpClient(mock_config)
            await client.connect()

            with pytest.raises(McpToolExecutionError, match="missing 'tools'"):
                await client.list_tools()

    async def test_list_tools_not_list(
        self, mock_config: McpConnectionConfig, mock_process: Mock
    ) -> None:
        """Test list_tools with non-list tools field."""
        init_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"protocolVersion": "2024-11-05", "capabilities": {}},
        }
        list_response = {"jsonrpc": "2.0", "id": 2, "result": {"tools": "not-a-list"}}

        responses = [
            (json.dumps(init_response) + "\n").encode("utf-8"),
            (json.dumps(list_response) + "\n").encode("utf-8"),
        ]
        mock_process.stdout.readline.side_effect = responses

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            client = StdioMcpClient(mock_config)
            await client.connect()

            with pytest.raises(McpToolExecutionError, match="not a list"):
                await client.list_tools()

    async def test_call_tool_success(
        self, mock_config: McpConnectionConfig, mock_process: Mock
    ) -> None:
        """Test successful tool execution."""
        init_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"protocolVersion": "2024-11-05", "capabilities": {}},
        }
        call_response = {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "content": [{"type": "text", "text": "File contents"}],
                "isError": False,
            },
        }

        responses = [
            (json.dumps(init_response) + "\n").encode("utf-8"),
            (json.dumps(call_response) + "\n").encode("utf-8"),
        ]
        mock_process.stdout.readline.side_effect = responses

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            client = StdioMcpClient(mock_config)
            await client.connect()
            result = await client.call_tool("read_file", {"path": "/tmp/test.txt"})

            assert result["isError"] is False
            assert result["content"][0]["text"] == "File contents"

    async def test_call_tool_not_connected(
        self, mock_config: McpConnectionConfig
    ) -> None:
        """Test call_tool when not connected."""
        client = StdioMcpClient(mock_config)

        with pytest.raises(McpConnectionError, match="not connected"):
            await client.call_tool("read_file", {})

    async def test_call_tool_invalid_response(
        self, mock_config: McpConnectionConfig, mock_process: Mock
    ) -> None:
        """Test call_tool with invalid response format."""
        init_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"protocolVersion": "2024-11-05", "capabilities": {}},
        }
        call_response = {"jsonrpc": "2.0", "id": 2, "result": "not-a-dict"}

        responses = [
            (json.dumps(init_response) + "\n").encode("utf-8"),
            (json.dumps(call_response) + "\n").encode("utf-8"),
        ]
        mock_process.stdout.readline.side_effect = responses

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            client = StdioMcpClient(mock_config)
            await client.connect()

            with pytest.raises(McpToolExecutionError, match="expected dict"):
                await client.call_tool("read_file", {})

    async def test_call_tool_with_error(
        self, mock_config: McpConnectionConfig, mock_process: Mock
    ) -> None:
        """Test tool execution that returns an error."""
        init_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"protocolVersion": "2024-11-05", "capabilities": {}},
        }
        call_response = {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "content": [{"type": "text", "text": "File not found"}],
                "isError": True,
            },
        }

        responses = [
            (json.dumps(init_response) + "\n").encode("utf-8"),
            (json.dumps(call_response) + "\n").encode("utf-8"),
        ]
        mock_process.stdout.readline.side_effect = responses

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            client = StdioMcpClient(mock_config)
            await client.connect()
            result = await client.call_tool("read_file", {"path": "/nonexistent"})

            assert result["isError"] is True
            assert "File not found" in result["content"][0]["text"]

    async def test_connection_timeout_config(
        self, mock_config: McpConnectionConfig
    ) -> None:
        """Test custom connection timeout."""
        client = StdioMcpClient(mock_config, connection_timeout=5.0, tool_timeout=20.0)
        assert client.connection_timeout == 5.0
        assert client.tool_timeout == 20.0

    async def test_is_connected_property(
        self, mock_config: McpConnectionConfig, mock_process: Mock
    ) -> None:
        """Test is_connected property tracks state correctly."""
        init_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"protocolVersion": "2024-11-05", "capabilities": {}},
        }
        response_bytes = (json.dumps(init_response) + "\n").encode("utf-8")
        mock_process.stdout.readline.return_value = response_bytes

        with patch("asyncio.create_subprocess_exec", return_value=mock_process):
            client = StdioMcpClient(mock_config)

            assert client.is_connected is False

            await client.connect()
            assert client.is_connected is True

            mock_process.returncode = 0
            await client.disconnect()
            assert client.is_connected is False
