"""Tests for MCP tool discovery and mapping.

Phase 4: Tool Discovery tests covering:
- MCP tool schema → ToolDefinition mapping (name prefixing, parameters)
- Tool origin metadata preservation (source, server_name, transport, original_name)
- Capability validation (missing tools/list or tools/call → McpDiscoveryError)
- Empty tool list handling (no crash)
- Name collision prevention (two servers with same tool name → distinct prefixed names)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.ai.mcp.discovery import McpToolDiscovery
from app.ai.mcp.exceptions import McpDiscoveryError
from app.ai.tools.schemas import ToolDefinition

pytestmark = pytest.mark.anyio


@pytest.fixture
def mock_client() -> AsyncMock:
    """Return a mock McpClient with async methods."""
    client = AsyncMock()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.list_tools = AsyncMock(return_value=[])
    client.call_tool = AsyncMock(return_value={"success": True})
    return client


@pytest.fixture
def sample_mcp_tool() -> dict[str, Any]:
    """Return a sample MCP tool schema."""
    return {
        "name": "read_file",
        "description": "Read contents of a file",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file",
                }
            },
            "required": ["path"],
        },
    }


class TestToolDiscovery:
    """Test MCP tool discovery and ToolDefinition mapping."""

    async def test_discover_success_single_tool(
        self,
        mock_client: AsyncMock,
        sample_mcp_tool: dict[str, Any],
    ) -> None:
        """Test successful discovery of a single tool."""
        mock_client.list_tools.return_value = [sample_mcp_tool]

        results = await McpToolDiscovery.discover(mock_client, "filesystem")

        assert len(results) == 1
        tool_def, adapter = results[0]

        # Verify ToolDefinition
        assert isinstance(tool_def, ToolDefinition)
        assert tool_def.name == "filesystem.read_file"
        assert tool_def.description == "Read contents of a file"
        assert tool_def.parameters["type"] == "object"
        assert "path" in tool_def.parameters["properties"]

        # Verify adapter
        assert adapter.server_name == "filesystem"
        assert adapter.tool_name == "read_file"
        assert adapter.client is mock_client
        assert adapter.metadata["source"] == "mcp"
        assert adapter.metadata["server_name"] == "filesystem"
        assert adapter.metadata["transport"] == "stdio"
        assert adapter.metadata["original_name"] == "read_file"

    async def test_discover_success_multiple_tools(
        self,
        mock_client: AsyncMock,
    ) -> None:
        """Test successful discovery of multiple tools."""
        mock_client.list_tools.return_value = [
            {
                "name": "read_file",
                "description": "Read a file",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "write_file",
                "description": "Write to a file",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "list_directory",
                "description": "List directory contents",
                "inputSchema": {"type": "object", "properties": {}},
            },
        ]

        results = await McpToolDiscovery.discover(mock_client, "filesystem")

        assert len(results) == 3
        names = [tool_def.name for tool_def, _ in results]
        assert "filesystem.read_file" in names
        assert "filesystem.write_file" in names
        assert "filesystem.list_directory" in names

    async def test_discover_empty_tool_list(
        self,
        mock_client: AsyncMock,
    ) -> None:
        """Test discovery with empty tool list returns empty result without crash."""
        mock_client.list_tools.return_value = []

        results = await McpToolDiscovery.discover(mock_client, "empty-server")

        assert results == []

    async def test_discover_list_tools_raises_exception(
        self,
        mock_client: AsyncMock,
    ) -> None:
        """Test that list_tools exception is wrapped in McpDiscoveryError."""
        mock_client.list_tools.side_effect = Exception("Connection timeout")

        with pytest.raises(McpDiscoveryError, match="Failed to discover tools"):
            await McpToolDiscovery.discover(mock_client, "failing-server")

    async def test_discover_invalid_tool_skipped(
        self,
        mock_client: AsyncMock,
    ) -> None:
        """Test that invalid tools are skipped with warning, not crash."""
        mock_client.list_tools.return_value = [
            {"name": "valid_tool", "description": "Valid", "inputSchema": {}},
            {"description": "Missing name field"},  # Invalid: no name
            {"name": "another_valid", "description": "Valid", "inputSchema": {}},
        ]

        results = await McpToolDiscovery.discover(mock_client, "partial-server")

        # Only valid tools should be included
        assert len(results) == 2
        names = [tool_def.name for tool_def, _ in results]
        assert "partial-server.valid_tool" in names
        assert "partial-server.another_valid" in names


class TestNamePrefixing:
    """Test tool name prefixing for collision prevention."""

    async def test_name_prefixing_single_server(
        self,
        mock_client: AsyncMock,
    ) -> None:
        """Test that tool names are prefixed with server name."""
        mock_client.list_tools.return_value = [
            {"name": "read_file", "description": "Read", "inputSchema": {}},
        ]

        results = await McpToolDiscovery.discover(mock_client, "filesystem")

        tool_def, adapter = results[0]
        assert tool_def.name == "filesystem.read_file"
        assert adapter.metadata["original_name"] == "read_file"

    async def test_collision_prevention_multiple_servers(
        self,
        mock_client: AsyncMock,
    ) -> None:
        """Test that identical tool names from different servers have distinct prefixes."""
        # Simulate discovery from two different servers with same tool name
        mock_client.list_tools.return_value = [
            {
                "name": "read_file",
                "description": "Read from filesystem",
                "inputSchema": {},
            },
        ]

        results_fs = await McpToolDiscovery.discover(mock_client, "filesystem")

        mock_client.list_tools.return_value = [
            {"name": "read_file", "description": "Read from GitHub", "inputSchema": {}},
        ]

        results_github = await McpToolDiscovery.discover(mock_client, "github")

        # Both tools have same original name but different prefixed names
        tool_def_fs, adapter_fs = results_fs[0]
        tool_def_github, adapter_github = results_github[0]

        assert tool_def_fs.name == "filesystem.read_file"
        assert tool_def_github.name == "github.read_file"
        assert adapter_fs.metadata["original_name"] == "read_file"
        assert adapter_github.metadata["original_name"] == "read_file"

        # Verify they are distinct
        assert tool_def_fs.name != tool_def_github.name


class TestOriginMetadata:
    """Test preservation of tool origin metadata."""

    async def test_metadata_preserved(
        self,
        mock_client: AsyncMock,
        sample_mcp_tool: dict[str, Any],
    ) -> None:
        """Test that tool origin metadata is preserved in adapter."""
        mock_client.list_tools.return_value = [sample_mcp_tool]

        results = await McpToolDiscovery.discover(mock_client, "test-server")

        _, adapter = results[0]

        assert adapter.metadata["source"] == "mcp"
        assert adapter.metadata["server_name"] == "test-server"
        assert adapter.metadata["transport"] == "stdio"
        assert adapter.metadata["original_name"] == "read_file"

    async def test_metadata_different_servers(
        self,
        mock_client: AsyncMock,
    ) -> None:
        """Test that metadata correctly reflects different server names."""
        mock_client.list_tools.return_value = [
            {"name": "tool1", "description": "Test", "inputSchema": {}},
        ]

        results_1 = await McpToolDiscovery.discover(mock_client, "server-1")
        results_2 = await McpToolDiscovery.discover(mock_client, "server-2")

        _, adapter_1 = results_1[0]
        _, adapter_2 = results_2[0]

        assert adapter_1.metadata["server_name"] == "server-1"
        assert adapter_2.metadata["server_name"] == "server-2"


class TestSchemaMapping:
    """Test MCP inputSchema → ToolDefinition.parameters mapping."""

    async def test_input_schema_preserved(
        self,
        mock_client: AsyncMock,
    ) -> None:
        """Test that inputSchema is correctly mapped to parameters."""
        mock_client.list_tools.return_value = [
            {
                "name": "complex_tool",
                "description": "A complex tool",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "string_arg": {"type": "string", "description": "A string"},
                        "number_arg": {"type": "number", "description": "A number"},
                        "array_arg": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "An array",
                        },
                    },
                    "required": ["string_arg"],
                },
            }
        ]

        results = await McpToolDiscovery.discover(mock_client, "test-server")

        tool_def, _ = results[0]

        assert tool_def.parameters["type"] == "object"
        assert "string_arg" in tool_def.parameters["properties"]
        assert "number_arg" in tool_def.parameters["properties"]
        assert "array_arg" in tool_def.parameters["properties"]
        assert tool_def.parameters["required"] == ["string_arg"]

    async def test_empty_input_schema_normalized(
        self,
        mock_client: AsyncMock,
    ) -> None:
        """Test that empty or missing inputSchema is normalized to valid schema."""
        mock_client.list_tools.return_value = [
            {"name": "no_schema", "description": "No schema"},
            {"name": "empty_schema", "description": "Empty", "inputSchema": {}},
        ]

        results = await McpToolDiscovery.discover(mock_client, "test-server")

        assert len(results) == 2

        # Both should have normalized schemas
        for tool_def, _ in results:
            assert tool_def.parameters["type"] == "object"
            assert "properties" in tool_def.parameters

    async def test_description_field_preserved(
        self,
        mock_client: AsyncMock,
    ) -> None:
        """Test that tool descriptions are preserved."""
        description = "This is a detailed tool description"
        mock_client.list_tools.return_value = [
            {
                "name": "test_tool",
                "description": description,
                "inputSchema": {},
            }
        ]

        results = await McpToolDiscovery.discover(mock_client, "test-server")

        tool_def, _ = results[0]
        assert tool_def.description == description

    async def test_missing_description_defaults_empty(
        self,
        mock_client: AsyncMock,
    ) -> None:
        """Test that missing description defaults to empty string."""
        mock_client.list_tools.return_value = [
            {"name": "no_desc_tool", "inputSchema": {}},
        ]

        results = await McpToolDiscovery.discover(mock_client, "test-server")

        tool_def, _ = results[0]
        assert tool_def.description == ""


class TestCapabilityValidation:
    """Test MCP server capability validation."""

    async def test_capabilities_validated_before_discovery(
        self,
        mock_client: AsyncMock,
    ) -> None:
        """Test that capabilities are validated before attempting discovery."""
        mock_client.list_tools.return_value = []

        # Should succeed with valid client (has list_tools and call_tool methods)
        results = await McpToolDiscovery.discover(mock_client, "test-server")

        assert results == []

    async def test_missing_list_tools_method_raises(self) -> None:
        """Test that client missing list_tools method raises McpDiscoveryError."""
        # Create client without list_tools method
        invalid_client = AsyncMock(spec=["connect", "disconnect", "call_tool"])

        with pytest.raises(McpDiscoveryError, match="missing required methods"):
            await McpToolDiscovery.discover(invalid_client, "invalid-server")

    async def test_missing_call_tool_method_raises(self) -> None:
        """Test that client missing call_tool method raises McpDiscoveryError."""
        # Create client without call_tool method
        invalid_client = AsyncMock(spec=["connect", "disconnect", "list_tools"])

        with pytest.raises(McpDiscoveryError, match="missing required methods"):
            await McpToolDiscovery.discover(invalid_client, "invalid-server")

    async def test_missing_both_methods_raises(self) -> None:
        """Test that client missing both required methods raises McpDiscoveryError."""
        # Create client without required methods
        invalid_client = AsyncMock(spec=["connect", "disconnect"])

        with pytest.raises(McpDiscoveryError, match="missing required methods"):
            await McpToolDiscovery.discover(invalid_client, "invalid-server")


class TestEdgeCases:
    """Test edge cases and error handling."""

    async def test_tool_with_special_characters_in_name(
        self,
        mock_client: AsyncMock,
    ) -> None:
        """Test that tool names with special characters are handled correctly."""
        mock_client.list_tools.return_value = [
            {
                "name": "read-file_v2",
                "description": "Read file v2",
                "inputSchema": {},
            }
        ]

        results = await McpToolDiscovery.discover(mock_client, "filesystem")

        tool_def, _ = results[0]
        assert tool_def.name == "filesystem.read-file_v2"

    async def test_tool_with_unicode_in_description(
        self,
        mock_client: AsyncMock,
    ) -> None:
        """Test that Unicode characters in descriptions are preserved."""
        mock_client.list_tools.return_value = [
            {
                "name": "test_tool",
                "description": "读取文件 — Read file with 日本語 support",
                "inputSchema": {},
            }
        ]

        results = await McpToolDiscovery.discover(mock_client, "test-server")

        tool_def, _ = results[0]
        assert "读取文件" in tool_def.description
        assert "日本語" in tool_def.description

    async def test_tool_with_nested_schema(
        self,
        mock_client: AsyncMock,
    ) -> None:
        """Test that nested input schemas are preserved correctly."""
        mock_client.list_tools.return_value = [
            {
                "name": "nested_tool",
                "description": "Tool with nested schema",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "config": {
                            "type": "object",
                            "properties": {
                                "host": {"type": "string"},
                                "port": {"type": "number"},
                            },
                            "required": ["host"],
                        }
                    },
                    "required": ["config"],
                },
            }
        ]

        results = await McpToolDiscovery.discover(mock_client, "test-server")

        tool_def, _ = results[0]
        config_prop = tool_def.parameters["properties"]["config"]
        assert config_prop["type"] == "object"
        assert "host" in config_prop["properties"]
        assert "port" in config_prop["properties"]

    async def test_multiple_discoveries_same_client(
        self,
        mock_client: AsyncMock,
    ) -> None:
        """Test that multiple discoveries on same client work correctly."""
        mock_client.list_tools.return_value = [
            {"name": "tool1", "description": "Tool 1", "inputSchema": {}},
        ]

        results_1 = await McpToolDiscovery.discover(mock_client, "server-1")
        results_2 = await McpToolDiscovery.discover(mock_client, "server-1")

        assert len(results_1) == 1
        assert len(results_2) == 1

        # Results should be independent
        tool_def_1, adapter_1 = results_1[0]
        tool_def_2, adapter_2 = results_2[0]

        assert tool_def_1.name == tool_def_2.name
        assert adapter_1 is not adapter_2  # Different adapter instances
