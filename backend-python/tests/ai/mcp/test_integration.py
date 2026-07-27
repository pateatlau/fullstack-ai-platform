"""Phase 9: Integration tests for MCP end-to-end workflow.

Tests MCP server registration, tool discovery, invocation via ToolExecutor,
permission enforcement, and shutdown flow with fake MCP servers.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest

from app.ai.mcp.config import McpConnectionConfig
from app.ai.mcp.exceptions import McpConnectionError
from app.ai.mcp.permissions import McpPermissionPolicy
from app.ai.mcp.registry import McpServerRegistry, ServerStatus
from app.ai.tools.executor import ToolExecutor
from app.ai.tools.registry import ToolRegistry
from app.ai.tools.registration import register_mcp_tools
from app.ai.tools.schemas import ToolCall, ToolExecutionContext
from app.core.caller import CallerContext
from app.core.config import Settings

pytestmark = pytest.mark.anyio


class FakeMcpClient:
    """Fake MCP client for testing end-to-end flow."""

    def __init__(
        self,
        server_name: str,
        tools: list[dict[str, Any]] | None = None,
        tool_results: dict[str, dict[str, Any]] | None = None,
    ):
        self.server_name = server_name
        self._tools = tools or []
        self._tool_results = tool_results or {}
        self._connected = False

    async def connect(self) -> None:
        """Simulate connection."""
        await asyncio.sleep(0.001)  # Simulate async operation
        self._connected = True

    async def disconnect(self) -> None:
        """Simulate disconnection."""
        await asyncio.sleep(0.001)
        self._connected = False

    async def list_tools(self) -> list[dict[str, Any]]:
        """Return fake tool list (directly as list, not wrapped in dict)."""
        return self._tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Return fake tool result or raise error if isError is true."""
        if name in self._tool_results:
            result = self._tool_results[name]
            # Check if this is an error result
            if result.get("isError", False):
                from app.ai.mcp.exceptions import McpToolExecutionError

                error_text = result.get("content", [{}])[0].get(
                    "text", "Tool execution failed"
                )
                raise McpToolExecutionError(error_text)
            return result
        return {
            "content": [{"type": "text", "text": f"Result from {name}"}],
            "isError": False,
        }


@pytest.fixture
def fake_settings() -> Settings:
    """Create Settings with MCP enabled for testing."""
    settings = Settings(
        mcp_enabled=True,
        mcp_servers=[],
        mcp_permission_policy={},
        mcp_connection_timeout_seconds=10,
        mcp_tool_timeout_seconds=30,
    )
    return settings


@pytest.fixture
def tool_registry() -> ToolRegistry:
    """Fresh ToolRegistry for each test."""
    return ToolRegistry()


@pytest.fixture
def mcp_registry(fake_settings: Settings) -> McpServerRegistry:
    """Fresh McpServerRegistry for each test."""
    return McpServerRegistry(
        connection_timeout=float(fake_settings.mcp_connection_timeout_seconds),
        tool_timeout=float(fake_settings.mcp_tool_timeout_seconds),
    )


async def test_mcp_integration_successful_registration_and_invocation(
    tool_registry: ToolRegistry,
    mcp_registry: McpServerRegistry,
    fake_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
):
    """Test successful MCP server registration, discovery, and tool invocation."""
    # Setup fake MCP client with sample tools
    fake_client = FakeMcpClient(
        server_name="test_server",
        tools=[
            {
                "name": "echo",
                "description": "Echo back the input",
                "inputSchema": {
                    "type": "object",
                    "properties": {"message": {"type": "string"}},
                    "required": ["message"],
                },
            }
        ],
        tool_results={"echo": {"content": [{"type": "text", "text": "Hello, World!"}]}},
    )

    # Monkeypatch StdioMcpClient to return fake client
    async def fake_register(
        self_registry, server_name: str, config: McpConnectionConfig
    ):
        client = fake_client
        self_registry._clients[server_name] = client
        self_registry._statuses[server_name] = ServerStatus.CONNECTED
        await client.connect()

    monkeypatch.setattr(McpServerRegistry, "register", fake_register)

    # Configure settings with test server
    fake_settings.mcp_servers = [
        {
            "name": "test_server",
            "command": "fake_command",
            "args": [],
            "env": {},
            "transport": "stdio",
        }
    ]

    # Register MCP tools
    await register_mcp_tools(
        registry=tool_registry,
        mcp_registry=mcp_registry,
        settings=fake_settings,
    )

    # Verify tool was registered with prefixed name
    assert tool_registry.get("test_server.echo") is not None

    # Create ToolExecutor with MCP permission policy
    permission_policy = McpPermissionPolicy(config={})
    tool_executor = ToolExecutor(
        registry=tool_registry,
        settings=fake_settings,
        mcp_permission_policy=permission_policy,
    )

    # Execute tool via ToolExecutor
    call = ToolCall(name="test_server.echo", arguments={"message": "test"})
    context = ToolExecutionContext(
        caller=CallerContext.for_user(user_id=uuid.uuid4()),
        request_id="test_req_123",
    )

    result = await tool_executor.execute(call, context)

    # Verify result
    assert result.success is True
    assert "Hello, World!" in str(result.data)
    assert result.metadata["tool_name"] == "test_server.echo"


async def test_mcp_integration_permission_denied(
    tool_registry: ToolRegistry,
    mcp_registry: McpServerRegistry,
    fake_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
):
    """Test MCP tool invocation denied by permission policy."""
    # Setup fake MCP client
    fake_client = FakeMcpClient(
        server_name="restricted_server",
        tools=[
            {
                "name": "restricted_tool",
                "description": "A restricted tool",
                "inputSchema": {"type": "object", "properties": {}},
            }
        ],
    )

    async def fake_register(
        self_registry, server_name: str, config: McpConnectionConfig
    ):
        client = fake_client
        self_registry._clients[server_name] = client
        self_registry._statuses[server_name] = ServerStatus.CONNECTED
        await client.connect()

    monkeypatch.setattr(McpServerRegistry, "register", fake_register)

    # Configure settings with restricted server (allowed at server level)
    fake_settings.mcp_servers = [
        {
            "name": "restricted_server",
            "command": "fake_command",
            "args": [],
            "env": {},
            "transport": "stdio",
        }
    ]

    # Configure permission policy to deny the specific tool
    fake_settings.mcp_permission_policy = {
        "allowed_servers": ["restricted_server"],
        "allowed_tools": {
            "restricted_server": ["other_tool"]  # restricted_tool not in list
        },
    }

    # Register MCP tools (tool should be skipped due to permission policy)
    await register_mcp_tools(
        registry=tool_registry,
        mcp_registry=mcp_registry,
        settings=fake_settings,
    )

    # Verify tool was NOT registered (denied by permission policy during registration)
    assert tool_registry.get("restricted_server.restricted_tool") is None


async def test_mcp_integration_connection_error_graceful_skip(
    tool_registry: ToolRegistry,
    mcp_registry: McpServerRegistry,
    fake_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
):
    """Test graceful skip when MCP server connection fails."""

    async def fake_register_fail(
        self_registry, server_name: str, config: McpConnectionConfig
    ):
        raise McpConnectionError(f"Connection to {server_name} failed")

    monkeypatch.setattr(McpServerRegistry, "register", fake_register_fail)

    # Configure settings with failing server
    fake_settings.mcp_servers = [
        {
            "name": "failing_server",
            "command": "fake_command",
            "args": [],
            "env": {},
            "transport": "stdio",
        }
    ]

    # Register MCP tools (should not crash)
    await register_mcp_tools(
        registry=tool_registry,
        mcp_registry=mcp_registry,
        settings=fake_settings,
    )

    # Verify no tools were registered
    assert len(tool_registry.list_tools()) == 0


async def test_mcp_integration_shutdown_flow(
    mcp_registry: McpServerRegistry,
    monkeypatch: pytest.MonkeyPatch,
):
    """Test graceful shutdown disconnects all servers."""
    # Setup fake MCP clients
    fake_client_1 = FakeMcpClient("server1")
    fake_client_2 = FakeMcpClient("server2")

    clients = {"server1": fake_client_1, "server2": fake_client_2}

    async def fake_register(
        self_registry, server_name: str, config: McpConnectionConfig
    ):
        client = clients[server_name]
        self_registry._clients[server_name] = client
        self_registry._statuses[server_name] = ServerStatus.CONNECTED
        await client.connect()

    monkeypatch.setattr(McpServerRegistry, "register", fake_register)

    # Register two servers
    config1 = McpConnectionConfig(
        name="server1", command="cmd1", args=[], env={}, transport="stdio"
    )
    config2 = McpConnectionConfig(
        name="server2", command="cmd2", args=[], env={}, transport="stdio"
    )

    await mcp_registry.register("server1", config1)
    await mcp_registry.register("server2", config2)

    # Verify both connected
    assert fake_client_1._connected is True
    assert fake_client_2._connected is True

    # Shutdown all
    await mcp_registry.disconnect_all()

    # Verify both disconnected
    assert fake_client_1._connected is False
    assert fake_client_2._connected is False
    assert mcp_registry.get("server1") is None
    assert mcp_registry.get("server2") is None


async def test_mcp_integration_flag_off_skips_registration(
    tool_registry: ToolRegistry,
    mcp_registry: McpServerRegistry,
):
    """Test that MCP tools are not registered when flag is off."""
    # Create settings with MCP disabled
    settings = Settings(
        mcp_enabled=False,
        mcp_servers=[
            {
                "name": "test_server",
                "command": "fake_command",
                "args": [],
                "env": {},
                "transport": "stdio",
            }
        ],
    )

    # Register MCP tools (should skip)
    await register_mcp_tools(
        registry=tool_registry,
        mcp_registry=mcp_registry,
        settings=settings,
    )

    # Verify no tools were registered
    assert len(tool_registry.list_tools()) == 0


async def test_mcp_integration_tool_execution_error(
    tool_registry: ToolRegistry,
    mcp_registry: McpServerRegistry,
    fake_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
):
    """Test tool execution error handling."""
    # Setup fake MCP client that returns error
    fake_client = FakeMcpClient(
        server_name="error_server",
        tools=[
            {
                "name": "failing_tool",
                "description": "A tool that fails",
                "inputSchema": {"type": "object", "properties": {}},
            }
        ],
        tool_results={
            "failing_tool": {
                "content": [{"type": "text", "text": "Tool execution failed"}],
                "isError": True,
            }
        },
    )

    async def fake_register(
        self_registry, server_name: str, config: McpConnectionConfig
    ):
        client = fake_client
        self_registry._clients[server_name] = client
        self_registry._statuses[server_name] = ServerStatus.CONNECTED
        await client.connect()

    monkeypatch.setattr(McpServerRegistry, "register", fake_register)

    # Configure settings
    fake_settings.mcp_servers = [
        {
            "name": "error_server",
            "command": "fake_command",
            "args": [],
            "env": {},
            "transport": "stdio",
        }
    ]

    # Register MCP tools
    await register_mcp_tools(
        registry=tool_registry,
        mcp_registry=mcp_registry,
        settings=fake_settings,
    )

    # Execute tool via ToolExecutor
    tool_executor = ToolExecutor(
        registry=tool_registry,
        settings=fake_settings,
    )

    call = ToolCall(name="error_server.failing_tool", arguments={})
    context = ToolExecutionContext(
        caller=CallerContext.for_user(user_id=uuid.uuid4()),
        request_id="test_req_123",
    )

    result = await tool_executor.execute(call, context)

    # Verify error result
    assert result.success is False
    assert result.error_code == "mcp_error"


async def test_mcp_integration_multiple_servers(
    tool_registry: ToolRegistry,
    mcp_registry: McpServerRegistry,
    fake_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
):
    """Test registration of tools from multiple MCP servers."""
    # Setup two fake MCP clients
    fake_client_1 = FakeMcpClient(
        server_name="server1",
        tools=[
            {
                "name": "tool1",
                "description": "Tool from server 1",
                "inputSchema": {"type": "object", "properties": {}},
            }
        ],
    )
    fake_client_2 = FakeMcpClient(
        server_name="server2",
        tools=[
            {
                "name": "tool2",
                "description": "Tool from server 2",
                "inputSchema": {"type": "object", "properties": {}},
            }
        ],
    )

    clients = {"server1": fake_client_1, "server2": fake_client_2}

    async def fake_register(
        self_registry, server_name: str, config: McpConnectionConfig
    ):
        client = clients[server_name]
        self_registry._clients[server_name] = client
        self_registry._statuses[server_name] = ServerStatus.CONNECTED
        await client.connect()

    monkeypatch.setattr(McpServerRegistry, "register", fake_register)

    # Configure settings with two servers
    fake_settings.mcp_servers = [
        {
            "name": "server1",
            "command": "cmd1",
            "args": [],
            "env": {},
            "transport": "stdio",
        },
        {
            "name": "server2",
            "command": "cmd2",
            "args": [],
            "env": {},
            "transport": "stdio",
        },
    ]

    # Register MCP tools
    await register_mcp_tools(
        registry=tool_registry,
        mcp_registry=mcp_registry,
        settings=fake_settings,
    )

    # Verify both tools were registered with prefixed names
    assert tool_registry.get("server1.tool1") is not None
    assert tool_registry.get("server2.tool2") is not None
    assert len(tool_registry.list_tools()) == 2


async def test_mcp_integration_name_collision_prevention(
    tool_registry: ToolRegistry,
    mcp_registry: McpServerRegistry,
    fake_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
):
    """Test that server name prefixing prevents tool name collisions."""
    # Setup two servers with same tool name
    fake_client_1 = FakeMcpClient(
        server_name="filesystem_a",
        tools=[
            {
                "name": "read_file",
                "description": "Read from filesystem A",
                "inputSchema": {"type": "object", "properties": {}},
            }
        ],
    )
    fake_client_2 = FakeMcpClient(
        server_name="filesystem_b",
        tools=[
            {
                "name": "read_file",
                "description": "Read from filesystem B",
                "inputSchema": {"type": "object", "properties": {}},
            }
        ],
    )

    clients = {"filesystem_a": fake_client_1, "filesystem_b": fake_client_2}

    async def fake_register(
        self_registry, server_name: str, config: McpConnectionConfig
    ):
        client = clients[server_name]
        self_registry._clients[server_name] = client
        self_registry._statuses[server_name] = ServerStatus.CONNECTED
        await client.connect()

    monkeypatch.setattr(McpServerRegistry, "register", fake_register)

    # Configure settings with two servers
    fake_settings.mcp_servers = [
        {
            "name": "filesystem_a",
            "command": "cmd1",
            "args": [],
            "env": {},
            "transport": "stdio",
        },
        {
            "name": "filesystem_b",
            "command": "cmd2",
            "args": [],
            "env": {},
            "transport": "stdio",
        },
    ]

    # Register MCP tools
    await register_mcp_tools(
        registry=tool_registry,
        mcp_registry=mcp_registry,
        settings=fake_settings,
    )

    # Verify both tools were registered with distinct prefixed names
    tool_a = tool_registry.get("filesystem_a.read_file")
    tool_b = tool_registry.get("filesystem_b.read_file")
    assert tool_a is not None
    assert tool_b is not None
    assert tool_a.name == "filesystem_a.read_file"
    assert tool_b.name == "filesystem_b.read_file"
    assert tool_a.description != tool_b.description  # Different descriptions
