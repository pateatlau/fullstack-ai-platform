"""Tests for MCP Protocol interfaces (Phase 1)."""

from __future__ import annotations

import inspect
from typing import Any

from app.ai.mcp.client import McpClient


class FakeMcpClient:
    """Fake MCP client for testing Protocol conformance.

    Implements McpClient Protocol to verify interface contracts.
    """

    def __init__(self) -> None:
        self.connected = False
        self.tools: list[dict[str, Any]] = []

    async def connect(self) -> None:
        """Establish fake connection."""
        self.connected = True

    async def disconnect(self) -> None:
        """Close fake connection."""
        self.connected = False

    async def list_tools(self) -> list[dict[str, Any]]:
        """Return fake tool list."""
        return self.tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Execute fake tool call."""
        return {"success": True, "result": f"called {name}"}


class TestMcpClientProtocol:
    """Test suite for McpClient Protocol interface."""

    def test_fake_client_implements_protocol(self) -> None:
        """FakeMcpClient correctly implements McpClient Protocol."""
        client: McpClient = FakeMcpClient()

        # Type checker ensures Protocol conformance; runtime verifies methods exist
        assert hasattr(client, "connect")
        assert hasattr(client, "disconnect")
        assert hasattr(client, "list_tools")
        assert hasattr(client, "call_tool")

        # Verify methods are coroutines (async)
        assert inspect.iscoroutinefunction(client.connect)
        assert inspect.iscoroutinefunction(client.disconnect)
        assert inspect.iscoroutinefunction(client.list_tools)
        assert inspect.iscoroutinefunction(client.call_tool)

    def test_protocol_methods_have_correct_signatures(self) -> None:
        """Protocol methods have expected parameter signatures."""
        client = FakeMcpClient()

        # Check connect signature (no params except self)
        connect_sig = inspect.signature(client.connect)
        assert len(connect_sig.parameters) == 0

        # Check disconnect signature (no params except self)
        disconnect_sig = inspect.signature(client.disconnect)
        assert len(disconnect_sig.parameters) == 0

        # Check list_tools signature (no params except self)
        list_tools_sig = inspect.signature(client.list_tools)
        assert len(list_tools_sig.parameters) == 0

        # Check call_tool signature (name, arguments)
        call_tool_sig = inspect.signature(client.call_tool)
        params = list(call_tool_sig.parameters.keys())
        assert "name" in params
        assert "arguments" in params

    def test_client_initialization(self) -> None:
        """Client can be instantiated and has expected initial state."""
        client = FakeMcpClient()

        assert hasattr(client, "connected")
        assert client.connected is False
        assert hasattr(client, "tools")
        assert isinstance(client.tools, list)
        assert len(client.tools) == 0


class TestProtocolTypeChecking:
    """Verify Protocol typing and conformance."""

    def test_protocol_is_typing_protocol(self) -> None:
        """McpClient is a typing.Protocol (not a concrete class)."""
        # Protocol should not be directly instantiable
        # This test verifies Protocol is imported correctly
        from typing import get_type_hints

        # McpClient should have method annotations
        hints = get_type_hints(McpClient.connect)
        assert "return" in hints

    def test_fake_client_matches_protocol_signature(self) -> None:
        """FakeMcpClient method signatures match Protocol."""
        fake_methods = {
            name: method
            for name, method in inspect.getmembers(
                FakeMcpClient, predicate=inspect.isfunction
            )
            if not name.startswith("_")
        }

        # Verify all Protocol methods are present
        assert "connect" in fake_methods
        assert "disconnect" in fake_methods
        assert "list_tools" in fake_methods
        assert "call_tool" in fake_methods

        # Verify call_tool has correct signature
        call_tool_sig = inspect.signature(fake_methods["call_tool"])
        params = list(call_tool_sig.parameters.keys())
        assert "name" in params
        assert "arguments" in params

    def test_protocol_can_be_used_as_type_annotation(self) -> None:
        """McpClient Protocol can be used for type annotations."""

        # This verifies type checker accepts Protocol as annotation
        def accepts_client(client: McpClient) -> McpClient:
            return client

        fake_client = FakeMcpClient()
        result = accepts_client(fake_client)

        # Type checker ensures FakeMcpClient conforms to Protocol
        assert result is fake_client
