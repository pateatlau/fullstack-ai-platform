"""Tests for MCP server registry lifecycle and status tracking.

Phase 3: Server Registry tests covering:
- Registry lifecycle (register/unregister/get)
- Status transitions (CONNECTING → CONNECTED/FAILED → DISCONNECTED)
- Error handling (duplicate registration, missing servers)
- Graceful shutdown (disconnect_all)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.ai.mcp.config import McpConnectionConfig
from app.ai.mcp.exceptions import McpConnectionError
from app.ai.mcp.registry import McpServerRegistry, ServerStatus

pytestmark = pytest.mark.anyio


@pytest.fixture
def registry() -> McpServerRegistry:
    """Return a fresh MCP server registry for each test."""
    return McpServerRegistry(connection_timeout=10.0, tool_timeout=30.0)


@pytest.fixture
def sample_config() -> McpConnectionConfig:
    """Return a sample MCP connection config."""
    return McpConnectionConfig(
        name="test-server",
        command="echo",
        args=["hello"],
        env={},
        transport="stdio",
    )


@pytest.fixture
def mock_client() -> AsyncMock:
    """Return a mock McpClient with async methods."""
    client = AsyncMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.list_tools = AsyncMock(return_value=[])
    client.call_tool = AsyncMock(return_value={"success": True})
    return client


class TestRegistryLifecycle:
    """Test registry registration, unregistration, and retrieval operations."""

    async def test_register_success(
        self,
        registry: McpServerRegistry,
        sample_config: McpConnectionConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test successful server registration and connection."""
        mock_client = AsyncMock()
        mock_client.connect = AsyncMock()

        def mock_stdio_client(*args: Any, **kwargs: Any) -> AsyncMock:
            return mock_client

        monkeypatch.setattr("app.ai.mcp.registry.StdioMcpClient", mock_stdio_client)

        await registry.register("test-server", sample_config)

        assert registry.get("test-server") is mock_client
        assert registry.get_status("test-server") == ServerStatus.CONNECTED
        mock_client.connect.assert_awaited_once()

    async def test_register_duplicate_name_raises(
        self,
        registry: McpServerRegistry,
        sample_config: McpConnectionConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that registering duplicate server name raises ValueError."""
        mock_client = AsyncMock()
        mock_client.connect = AsyncMock()

        monkeypatch.setattr(
            "app.ai.mcp.registry.StdioMcpClient", lambda *args, **kwargs: mock_client
        )

        await registry.register("test-server", sample_config)

        with pytest.raises(ValueError, match="already registered"):
            await registry.register("test-server", sample_config)

    async def test_register_blocks_concurrent_registrations(
        self,
        registry: McpServerRegistry,
        sample_config: McpConnectionConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that concurrent registration attempts are blocked during CONNECTING."""
        # Manually set CONNECTING status to simulate in-flight registration
        registry._statuses["test-server"] = ServerStatus.CONNECTING

        with pytest.raises(ValueError, match="registration already in progress"):
            await registry.register("test-server", sample_config)

    async def test_register_allows_retry_after_failed(
        self,
        registry: McpServerRegistry,
        sample_config: McpConnectionConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that re-registration is allowed after FAILED status (retry scenario)."""
        mock_client = AsyncMock()
        mock_client.connect = AsyncMock()

        monkeypatch.setattr(
            "app.ai.mcp.registry.StdioMcpClient", lambda *args, **kwargs: mock_client
        )

        # Simulate a previous failed registration
        registry._statuses["test-server"] = ServerStatus.FAILED

        # Should succeed (allow retry)
        await registry.register("test-server", sample_config)

        assert registry.get_status("test-server") == ServerStatus.CONNECTED

    async def test_register_config_name_mismatch_raises(
        self,
        registry: McpServerRegistry,
    ) -> None:
        """Test that config.name != server_name raises ValueError."""
        config = McpConnectionConfig(
            name="different-name",
            command="echo",
            args=[],
            transport="stdio",
        )

        with pytest.raises(ValueError, match="Server name mismatch"):
            await registry.register("test-server", config)

    async def test_register_connection_failure_sets_failed_status(
        self,
        registry: McpServerRegistry,
        sample_config: McpConnectionConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that connection failure sets status to FAILED and raises."""
        mock_client = AsyncMock()
        mock_client.connect = AsyncMock(
            side_effect=McpConnectionError("Connection refused")
        )

        monkeypatch.setattr(
            "app.ai.mcp.registry.StdioMcpClient", lambda *args, **kwargs: mock_client
        )

        with pytest.raises(McpConnectionError, match="Failed to register"):
            await registry.register("test-server", sample_config)

        assert registry.get("test-server") is None
        assert registry.get_status("test-server") == ServerStatus.FAILED

    async def test_unregister_disconnects_and_removes(
        self,
        registry: McpServerRegistry,
        sample_config: McpConnectionConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that unregister disconnects client and removes from registry."""
        mock_client = AsyncMock()
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()

        monkeypatch.setattr(
            "app.ai.mcp.registry.StdioMcpClient", lambda *args, **kwargs: mock_client
        )

        await registry.register("test-server", sample_config)
        await registry.unregister("test-server")

        mock_client.disconnect.assert_awaited_once()
        assert registry.get("test-server") is None
        assert registry.get_status("test-server") == ServerStatus.DISCONNECTED

    async def test_unregister_missing_server_is_noop(
        self, registry: McpServerRegistry
    ) -> None:
        """Test that unregistering non-existent server is no-op (idempotent)."""
        await registry.unregister("nonexistent-server")

        assert registry.get("nonexistent-server") is None

    async def test_unregister_disconnect_error_logged_not_raised(
        self,
        registry: McpServerRegistry,
        sample_config: McpConnectionConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test that disconnect errors during unregister are logged but not raised."""
        mock_client = AsyncMock()
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock(side_effect=Exception("Disconnect failed"))

        monkeypatch.setattr(
            "app.ai.mcp.registry.StdioMcpClient", lambda *args, **kwargs: mock_client
        )

        await registry.register("test-server", sample_config)
        await registry.unregister("test-server")

        assert registry.get("test-server") is None
        assert registry.get_status("test-server") == ServerStatus.DISCONNECTED

    def test_get_missing_server_returns_none(self, registry: McpServerRegistry) -> None:
        """Test that get() returns None for non-existent server."""
        assert registry.get("nonexistent-server") is None

    def test_get_status_missing_server_returns_none(
        self, registry: McpServerRegistry
    ) -> None:
        """Test that get_status() returns None for never-registered server."""
        assert registry.get_status("nonexistent-server") is None


class TestStatusTracking:
    """Test server status transitions and tracking."""

    async def test_status_transitions_success_path(
        self,
        registry: McpServerRegistry,
        sample_config: McpConnectionConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test status transitions: CONNECTING → CONNECTED → DISCONNECTED."""
        mock_client = AsyncMock()
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()

        monkeypatch.setattr(
            "app.ai.mcp.registry.StdioMcpClient", lambda *args, **kwargs: mock_client
        )

        # Before registration: no status
        assert registry.get_status("test-server") is None

        # During/after registration: CONNECTED
        await registry.register("test-server", sample_config)
        assert registry.get_status("test-server") == ServerStatus.CONNECTED

        # After unregister: DISCONNECTED
        await registry.unregister("test-server")
        assert registry.get_status("test-server") == ServerStatus.DISCONNECTED

    async def test_status_transitions_failure_path(
        self,
        registry: McpServerRegistry,
        sample_config: McpConnectionConfig,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test status transitions: CONNECTING → FAILED."""
        mock_client = AsyncMock()
        mock_client.connect = AsyncMock(
            side_effect=McpConnectionError("Connection timeout")
        )

        monkeypatch.setattr(
            "app.ai.mcp.registry.StdioMcpClient", lambda *args, **kwargs: mock_client
        )

        with pytest.raises(McpConnectionError):
            await registry.register("test-server", sample_config)

        assert registry.get_status("test-server") == ServerStatus.FAILED

    async def test_list_servers_empty(self, registry: McpServerRegistry) -> None:
        """Test list_servers returns empty list for empty registry."""
        servers = registry.list_servers()
        assert servers == []

    async def test_list_servers_returns_active_servers_with_status(
        self,
        registry: McpServerRegistry,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test list_servers returns all registered servers with status."""
        mock_client_1 = AsyncMock()
        mock_client_1.connect = AsyncMock()
        mock_client_2 = AsyncMock()
        mock_client_2.connect = AsyncMock()

        call_count = 0

        def mock_stdio_client(*args: Any, **kwargs: Any) -> AsyncMock:
            nonlocal call_count
            call_count += 1
            return mock_client_1 if call_count == 1 else mock_client_2

        monkeypatch.setattr("app.ai.mcp.registry.StdioMcpClient", mock_stdio_client)

        config_1 = McpConnectionConfig(
            name="server-1", command="echo", args=[], transport="stdio"
        )
        config_2 = McpConnectionConfig(
            name="server-2", command="echo", args=[], transport="stdio"
        )

        await registry.register("server-1", config_1)
        await registry.register("server-2", config_2)

        servers = registry.list_servers()
        assert len(servers) == 2
        assert ("server-1", ServerStatus.CONNECTED) in servers
        assert ("server-2", ServerStatus.CONNECTED) in servers


class TestGracefulShutdown:
    """Test disconnect_all for app shutdown flow."""

    async def test_disconnect_all_empty_registry(
        self, registry: McpServerRegistry
    ) -> None:
        """Test disconnect_all on empty registry is no-op."""
        await registry.disconnect_all()
        assert registry.list_servers() == []

    async def test_disconnect_all_disconnects_all_servers(
        self,
        registry: McpServerRegistry,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test disconnect_all disconnects all registered servers in parallel."""
        mock_client_1 = AsyncMock()
        mock_client_1.connect = AsyncMock()
        mock_client_1.disconnect = AsyncMock()
        mock_client_2 = AsyncMock()
        mock_client_2.connect = AsyncMock()
        mock_client_2.disconnect = AsyncMock()

        call_count = 0

        def mock_stdio_client(*args: Any, **kwargs: Any) -> AsyncMock:
            nonlocal call_count
            call_count += 1
            return mock_client_1 if call_count == 1 else mock_client_2

        monkeypatch.setattr("app.ai.mcp.registry.StdioMcpClient", mock_stdio_client)

        config_1 = McpConnectionConfig(
            name="server-1", command="echo", args=[], transport="stdio"
        )
        config_2 = McpConnectionConfig(
            name="server-2", command="echo", args=[], transport="stdio"
        )

        await registry.register("server-1", config_1)
        await registry.register("server-2", config_2)

        await registry.disconnect_all()

        mock_client_1.disconnect.assert_awaited_once()
        mock_client_2.disconnect.assert_awaited_once()
        assert registry.list_servers() == []
        assert registry.get_status("server-1") == ServerStatus.DISCONNECTED
        assert registry.get_status("server-2") == ServerStatus.DISCONNECTED

    async def test_disconnect_all_logs_errors_does_not_raise(
        self,
        registry: McpServerRegistry,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test disconnect_all logs errors but does not raise exceptions."""
        mock_client = AsyncMock()
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock(side_effect=Exception("Disconnect failed"))

        monkeypatch.setattr(
            "app.ai.mcp.registry.StdioMcpClient", lambda *args, **kwargs: mock_client
        )

        config = McpConnectionConfig(
            name="test-server", command="echo", args=[], transport="stdio"
        )

        await registry.register("test-server", config)
        await registry.disconnect_all()

        mock_client.disconnect.assert_awaited_once()
        assert registry.list_servers() == []
        assert registry.get_status("test-server") == ServerStatus.DISCONNECTED

    async def test_disconnect_all_clears_registry(
        self,
        registry: McpServerRegistry,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test disconnect_all clears all clients from registry."""
        mock_client = AsyncMock()
        mock_client.connect = AsyncMock()
        mock_client.disconnect = AsyncMock()

        monkeypatch.setattr(
            "app.ai.mcp.registry.StdioMcpClient", lambda *args, **kwargs: mock_client
        )

        config = McpConnectionConfig(
            name="test-server", command="echo", args=[], transport="stdio"
        )

        await registry.register("test-server", config)
        assert registry.get("test-server") is not None

        await registry.disconnect_all()

        assert registry.get("test-server") is None
        assert registry.list_servers() == []


class TestDIFactory:
    """Test DI factory for singleton registry."""

    def test_get_mcp_server_registry_returns_singleton(self) -> None:
        """Test that get_mcp_server_registry returns cached singleton."""
        from app.ai.deps import get_mcp_server_registry

        registry_1 = get_mcp_server_registry()
        registry_2 = get_mcp_server_registry()

        assert registry_1 is registry_2
        assert isinstance(registry_1, McpServerRegistry)
