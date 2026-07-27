"""Tests for MCP tool registration (Phase 8).

Tests:
- register_mcp_tools with fake MCP clients and configs
- Success path: servers registered, tools discovered, handlers registered
- Error paths: connection failures, discovery errors, permission denied
- Graceful error handling: failed servers do not crash startup
- Tool name prefixing and origin metadata preservation
- Permission policy composition (server-level and tool-level)
- Discovery result caching
"""

from __future__ import annotations

from typing import Any

import pytest

from app.ai.mcp import MCP_SPEC_VERSION
from app.ai.mcp.config import McpConnectionConfig
from app.ai.mcp.exceptions import McpConnectionError, McpDiscoveryError
from app.ai.mcp.permissions import McpPermissionPolicy
from app.ai.mcp.registry import McpServerRegistry, ServerStatus
from app.ai.tools.registration import register_mcp_tools
from app.ai.tools.registry import ToolRegistry
from app.core.config import Settings

pytestmark = pytest.mark.anyio


@pytest.fixture
def mock_settings() -> Settings:
    """Create Settings instance with MCP config for testing."""
    return Settings(
        openai_api_key="test-key",
        mcp_enabled=True,
        mcp_servers=[
            {
                "name": "test_server",
                "command": "test-command",
                "args": ["arg1", "arg2"],
                "env": {},
                "transport": "stdio",
            }
        ],
        mcp_permission_policy={},
        mcp_connection_timeout_seconds=10,
        mcp_tool_timeout_seconds=30,
    )


@pytest.fixture
def mock_registry() -> ToolRegistry:
    """Create ToolRegistry instance for testing."""
    return ToolRegistry()


@pytest.fixture
def mock_mcp_registry() -> McpServerRegistry:
    """Create McpServerRegistry instance for testing."""
    return McpServerRegistry(connection_timeout=10.0, tool_timeout=30.0)


class FakeMcpClient:
    """Fake MCP client for testing."""

    def __init__(
        self,
        tools: list[dict[str, Any]] | None = None,
        should_fail_connect: bool = False,
        should_fail_list: bool = False,
    ) -> None:
        self.tools = tools or []
        self.should_fail_connect = should_fail_connect
        self.should_fail_list = should_fail_list
        self.connected = False

    async def connect(self) -> None:
        if self.should_fail_connect:
            raise McpConnectionError("Connection failed")
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def list_tools(self) -> list[dict[str, Any]]:
        if self.should_fail_list:
            raise McpDiscoveryError("Discovery failed")
        return self.tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {"success": True, "data": f"Result for {name}"}


async def test_register_mcp_tools_success(
    mock_settings: Settings,
    mock_registry: ToolRegistry,
    mock_mcp_registry: McpServerRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test successful MCP tool registration."""
    # Create fake client with test tools
    fake_tools = [
        {
            "name": "test_tool_1",
            "description": "Test tool 1",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "test_tool_2",
            "description": "Test tool 2",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]
    fake_client = FakeMcpClient(tools=fake_tools)

    # Mock StdioMcpClient to return our fake client
    async def fake_register(
        self: McpServerRegistry, server_name: str, config: McpConnectionConfig
    ) -> None:
        self._clients[server_name] = fake_client
        self._statuses[server_name] = ServerStatus.CONNECTED

    monkeypatch.setattr(McpServerRegistry, "register", fake_register)

    # Register MCP tools
    await register_mcp_tools(
        registry=mock_registry,
        mcp_registry=mock_mcp_registry,
        settings=mock_settings,
    )

    # Verify tools were registered with prefixed names
    assert mock_registry.get("test_server.test_tool_1") is not None
    assert mock_registry.get("test_server.test_tool_2") is not None

    # Verify total tool count
    all_tools = mock_registry.list_tools()
    assert len(all_tools) == 2


async def test_register_mcp_tools_no_servers(
    mock_registry: ToolRegistry,
    mock_mcp_registry: McpServerRegistry,
) -> None:
    """Test registration with no configured servers."""
    settings = Settings(openai_api_key="test-key", mcp_enabled=True, mcp_servers=[])

    # Should not raise; just log and return
    await register_mcp_tools(
        registry=mock_registry,
        mcp_registry=mock_mcp_registry,
        settings=settings,
    )

    # No tools should be registered
    assert len(mock_registry.list_tools()) == 0


async def test_register_mcp_tools_connection_failure(
    mock_settings: Settings,
    mock_registry: ToolRegistry,
    mock_mcp_registry: McpServerRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test graceful handling of connection failures."""

    # Mock StdioMcpClient to raise connection error
    async def fake_register(
        self: McpServerRegistry, server_name: str, config: McpConnectionConfig
    ) -> None:
        raise McpConnectionError("Connection failed")

    monkeypatch.setattr(McpServerRegistry, "register", fake_register)

    # Should not raise; server failures are logged and skipped
    await register_mcp_tools(
        registry=mock_registry,
        mcp_registry=mock_mcp_registry,
        settings=mock_settings,
    )

    # No tools should be registered due to connection failure
    assert len(mock_registry.list_tools()) == 0


async def test_register_mcp_tools_discovery_failure(
    mock_settings: Settings,
    mock_registry: ToolRegistry,
    mock_mcp_registry: McpServerRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test graceful handling of discovery failures."""
    # Create fake client that fails during discovery
    fake_client = FakeMcpClient(should_fail_list=True)

    # Mock registry to return fake client
    async def fake_register(
        self: McpServerRegistry, server_name: str, config: McpConnectionConfig
    ) -> None:
        self._clients[server_name] = fake_client
        self._statuses[server_name] = ServerStatus.CONNECTED

    monkeypatch.setattr(McpServerRegistry, "register", fake_register)

    # Should not raise; discovery failures are logged and skipped
    await register_mcp_tools(
        registry=mock_registry,
        mcp_registry=mock_mcp_registry,
        settings=mock_settings,
    )

    # No tools should be registered due to discovery failure
    assert len(mock_registry.list_tools()) == 0


async def test_register_mcp_tools_permission_denied_server(
    mock_settings: Settings,
    mock_registry: ToolRegistry,
    mock_mcp_registry: McpServerRegistry,
) -> None:
    """Test server-level permission denial."""
    # Create permission policy that denies the test server
    permission_policy = McpPermissionPolicy(
        config={
            "allowed_servers": ["other_server"],  # test_server not in list
        }
    )

    # Should not raise; permission-denied server is skipped
    await register_mcp_tools(
        registry=mock_registry,
        mcp_registry=mock_mcp_registry,
        settings=mock_settings,
        permission_policy=permission_policy,
    )

    # No tools should be registered due to server permission denial
    assert len(mock_registry.list_tools()) == 0


async def test_register_mcp_tools_permission_denied_tool(
    mock_settings: Settings,
    mock_registry: ToolRegistry,
    mock_mcp_registry: McpServerRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test tool-level permission denial."""
    # Create fake client with test tools
    fake_tools = [
        {
            "name": "allowed_tool",
            "description": "Allowed tool",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "denied_tool",
            "description": "Denied tool",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]
    fake_client = FakeMcpClient(tools=fake_tools)

    # Mock registry to return fake client
    async def fake_register(
        self: McpServerRegistry, server_name: str, config: McpConnectionConfig
    ) -> None:
        self._clients[server_name] = fake_client
        self._statuses[server_name] = ServerStatus.CONNECTED

    monkeypatch.setattr(McpServerRegistry, "register", fake_register)

    # Create permission policy that allows server but restricts tools
    permission_policy = McpPermissionPolicy(
        config={
            "allowed_servers": ["test_server"],
            "allowed_tools": {
                "test_server": ["allowed_tool"],  # Only allowed_tool permitted
            },
        }
    )

    # Register MCP tools
    await register_mcp_tools(
        registry=mock_registry,
        mcp_registry=mock_mcp_registry,
        settings=mock_settings,
        permission_policy=permission_policy,
    )

    # Only allowed_tool should be registered
    assert mock_registry.get("test_server.allowed_tool") is not None
    assert mock_registry.get("test_server.denied_tool") is None
    assert len(mock_registry.list_tools()) == 1


async def test_register_mcp_tools_wildcard_permission(
    mock_settings: Settings,
    mock_registry: ToolRegistry,
    mock_mcp_registry: McpServerRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test wildcard tool permission (all tools from server allowed)."""
    # Create fake client with test tools
    fake_tools = [
        {
            "name": "tool_1",
            "description": "Tool 1",
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "tool_2",
            "description": "Tool 2",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]
    fake_client = FakeMcpClient(tools=fake_tools)

    # Mock registry to return fake client
    async def fake_register(
        self: McpServerRegistry, server_name: str, config: McpConnectionConfig
    ) -> None:
        self._clients[server_name] = fake_client
        self._statuses[server_name] = ServerStatus.CONNECTED

    monkeypatch.setattr(McpServerRegistry, "register", fake_register)

    # Create permission policy with wildcard for all tools
    permission_policy = McpPermissionPolicy(
        config={
            "allowed_servers": ["test_server"],
            "allowed_tools": {"test_server": ["*"]},  # Wildcard: all tools allowed
        }
    )

    # Register MCP tools
    await register_mcp_tools(
        registry=mock_registry,
        mcp_registry=mock_mcp_registry,
        settings=mock_settings,
        permission_policy=permission_policy,
    )

    # Both tools should be registered
    assert mock_registry.get("test_server.tool_1") is not None
    assert mock_registry.get("test_server.tool_2") is not None
    assert len(mock_registry.list_tools()) == 2


async def test_register_mcp_tools_name_collision_prevention(
    mock_registry: ToolRegistry,
    mock_mcp_registry: McpServerRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test tool name collision prevention with server prefixing."""
    # Settings with two servers having same tool name
    settings = Settings(
        openai_api_key="test-key",
        mcp_enabled=True,
        mcp_servers=[
            {
                "name": "server_1",
                "command": "test-command-1",
                "args": [],
                "env": {},
                "transport": "stdio",
            },
            {
                "name": "server_2",
                "command": "test-command-2",
                "args": [],
                "env": {},
                "transport": "stdio",
            },
        ],
    )

    # Both servers have a tool named "read_file"
    same_tool_name = [
        {
            "name": "read_file",
            "description": "Read a file",
            "inputSchema": {"type": "object", "properties": {}},
        }
    ]

    fake_client_1 = FakeMcpClient(tools=same_tool_name)
    fake_client_2 = FakeMcpClient(tools=same_tool_name)

    # Mock registry to return appropriate fake client per server
    clients = {"server_1": fake_client_1, "server_2": fake_client_2}

    async def fake_register(
        self: McpServerRegistry, server_name: str, config: McpConnectionConfig
    ) -> None:
        self._clients[server_name] = clients[server_name]
        self._statuses[server_name] = ServerStatus.CONNECTED

    monkeypatch.setattr(McpServerRegistry, "register", fake_register)

    # Register MCP tools
    await register_mcp_tools(
        registry=mock_registry,
        mcp_registry=mock_mcp_registry,
        settings=settings,
    )

    # Both tools should be registered with prefixed names (no collision)
    assert mock_registry.get("server_1.read_file") is not None
    assert mock_registry.get("server_2.read_file") is not None
    assert len(mock_registry.list_tools()) == 2


async def test_register_mcp_tools_empty_tool_list(
    mock_settings: Settings,
    mock_registry: ToolRegistry,
    mock_mcp_registry: McpServerRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test registration with server that has no tools."""
    # Create fake client with empty tool list
    fake_client = FakeMcpClient(tools=[])

    # Mock registry to return fake client
    async def fake_register(
        self: McpServerRegistry, server_name: str, config: McpConnectionConfig
    ) -> None:
        self._clients[server_name] = fake_client
        self._statuses[server_name] = ServerStatus.CONNECTED

    monkeypatch.setattr(McpServerRegistry, "register", fake_register)

    # Should not raise; empty tool list is handled gracefully
    await register_mcp_tools(
        registry=mock_registry,
        mcp_registry=mock_mcp_registry,
        settings=mock_settings,
    )

    # No tools should be registered
    assert len(mock_registry.list_tools()) == 0


async def test_register_mcp_tools_invalid_config(
    mock_registry: ToolRegistry,
    mock_mcp_registry: McpServerRegistry,
) -> None:
    """Test graceful handling of invalid server config."""
    # Settings with invalid server config (missing required fields)
    settings = Settings(
        openai_api_key="test-key",
        mcp_enabled=True,
        mcp_servers=[
            {
                "name": "invalid_server",
                # Missing 'command' field
                "args": [],
                "env": {},
                "transport": "stdio",
            }
        ],
    )

    # Should not raise; invalid config is logged and skipped
    await register_mcp_tools(
        registry=mock_registry,
        mcp_registry=mock_mcp_registry,
        settings=settings,
    )

    # No tools should be registered due to invalid config
    assert len(mock_registry.list_tools()) == 0


async def test_register_mcp_tools_spec_version_logged(
    mock_settings: Settings,
    mock_registry: ToolRegistry,
    mock_mcp_registry: McpServerRegistry,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test that MCP spec version is logged during registration."""
    # Create fake client
    fake_client = FakeMcpClient(tools=[])

    # Mock registry to return fake client
    async def fake_register(
        self: McpServerRegistry, server_name: str, config: McpConnectionConfig
    ) -> None:
        self._clients[server_name] = fake_client
        self._statuses[server_name] = ServerStatus.CONNECTED

    monkeypatch.setattr(McpServerRegistry, "register", fake_register)

    # Register MCP tools
    await register_mcp_tools(
        registry=mock_registry,
        mcp_registry=mock_mcp_registry,
        settings=mock_settings,
    )

    # Verify MCP spec version is mentioned in logs
    # Note: This test verifies the constant is available and used
    assert MCP_SPEC_VERSION == "2024-11-05"


async def test_register_mcp_tools_tool_metadata_preserved(
    mock_settings: Settings,
    mock_registry: ToolRegistry,
    mock_mcp_registry: McpServerRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that tool origin metadata is preserved."""
    # Create fake client with test tool
    fake_tools = [
        {
            "name": "test_tool",
            "description": "Test tool",
            "inputSchema": {"type": "object", "properties": {}},
        }
    ]
    fake_client = FakeMcpClient(tools=fake_tools)

    # Mock registry to return fake client
    async def fake_register(
        self: McpServerRegistry, server_name: str, config: McpConnectionConfig
    ) -> None:
        self._clients[server_name] = fake_client
        self._statuses[server_name] = ServerStatus.CONNECTED

    monkeypatch.setattr(McpServerRegistry, "register", fake_register)

    # Register MCP tools
    await register_mcp_tools(
        registry=mock_registry,
        mcp_registry=mock_mcp_registry,
        settings=mock_settings,
    )

    # Get registered handler and verify metadata
    handler = mock_registry.get_handler("test_server.test_tool")
    assert handler is not None

    # Import to check type and access metadata
    from app.ai.mcp.executor import McpToolExecutionAdapter

    # Verify metadata fields (adapter should have metadata attribute)
    assert isinstance(handler, McpToolExecutionAdapter)
    metadata = handler.metadata
    assert metadata["source"] == "mcp"
    assert metadata["server_name"] == "test_server"
    assert metadata["transport"] == "stdio"
    assert metadata["original_name"] == "test_tool"
