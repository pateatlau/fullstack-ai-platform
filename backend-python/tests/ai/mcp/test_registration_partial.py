"""Test partial tool registration failure handling."""

from __future__ import annotations

from typing import Any

import pytest

from app.ai.mcp.config import McpConnectionConfig
from app.ai.mcp.registry import McpServerRegistry, ServerStatus
from app.ai.tools.registration import register_mcp_tools
from app.ai.tools.registry import ToolRegistry
from app.core.config import Settings
from tests.ai.mcp.test_registration import FakeMcpClient

pytestmark = pytest.mark.anyio


async def test_register_mcp_tools_partial_registration_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that one tool registration failure doesn't abort remaining tools."""
    # Create registries
    mock_registry = ToolRegistry()
    mock_mcp_registry = McpServerRegistry()

    # Settings with server that has multiple tools
    settings = Settings(
        openai_api_key="test-key",
        mcp_enabled=True,
        mcp_servers=[
            {
                "name": "test_server",
                "command": "test-command",
                "args": [],
                "env": {},
                "transport": "stdio",
            }
        ],
    )

    # Create fake client with 3 tools
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
        {
            "name": "tool_3",
            "description": "Tool 3",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]
    fake_client = FakeMcpClient(tools=fake_tools)

    # Mock registry to return fake client
    async def fake_register_server(
        self: McpServerRegistry, server_name: str, config: McpConnectionConfig
    ) -> None:
        self._clients[server_name] = fake_client
        self._statuses[server_name] = ServerStatus.CONNECTED

    monkeypatch.setattr(McpServerRegistry, "register", fake_register_server)

    # Make tool_2 registration fail
    original_register = mock_registry.register
    call_count = [0]

    def failing_register(tool_def: Any, handler: Any) -> None:
        call_count[0] += 1
        # Fail on the second tool
        if "tool_2" in tool_def.name:
            raise ValueError("Simulated registration failure for tool_2")
        original_register(tool_def, handler)

    monkeypatch.setattr(mock_registry, "register", failing_register)

    # Register MCP tools
    await register_mcp_tools(
        registry=mock_registry,
        mcp_registry=mock_mcp_registry,
        settings=settings,
    )

    # Verify tool_1 and tool_3 were registered (tool_2 failed)
    assert mock_registry.get("test_server.tool_1") is not None
    assert mock_registry.get("test_server.tool_2") is None  # Failed
    assert mock_registry.get("test_server.tool_3") is not None

    # Only 2 tools should be successfully registered
    assert len(mock_registry.list_tools()) == 2
