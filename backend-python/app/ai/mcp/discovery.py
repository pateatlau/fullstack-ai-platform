"""MCP tool discovery — map MCP tools/list to ToolDefinition.

Phase 4: Tool Discovery implementation.

Responsibilities:
- Validate MCP server capabilities (tools/list, tools/call required)
- Fetch tool list from MCP server via client.list_tools()
- Map MCP tool schema → ToolDefinition with prefixed names
- Preserve tool origin metadata (source, server_name, transport, original_name)
- Instantiate McpToolExecutionAdapter per discovered tool
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.ai.mcp.exceptions import McpDiscoveryError
from app.ai.tools.schemas import ToolDefinition

if TYPE_CHECKING:
    from app.ai.mcp.client import McpClient
    from app.ai.mcp.executor import McpToolExecutionAdapter
    from app.ai.security.audit.logger import AuditLogger
    from app.ai.security.guardrails.engine import GuardrailEngine
    from app.core.config import Settings

logger = logging.getLogger(__name__)


class McpToolDiscovery:
    """Discover and map MCP tools to ToolDefinition instances.

    Validates server capabilities, fetches tools, prefixes names to prevent
    collisions, and preserves origin metadata for observability.
    """

    @staticmethod
    async def discover(
        client: McpClient,
        server_name: str,
        *,
        settings: Settings | None = None,
        guardrail_engine: GuardrailEngine | None = None,
        audit_logger: AuditLogger | None = None,
    ) -> list[tuple[ToolDefinition, McpToolExecutionAdapter]]:
        """Discover tools from MCP server and map to ToolDefinition + adapter pairs.

        Args:
            client: Connected MCP client instance.
            server_name: MCP server name (used for tool name prefixing).

        Returns:
            List of (ToolDefinition, McpToolExecutionAdapter) tuples.

        Raises:
            McpDiscoveryError: If server capabilities are missing or tool discovery fails.
        """
        # Validate server capabilities
        await McpToolDiscovery._validate_capabilities(client, server_name)

        # Fetch tool list from server
        try:
            mcp_tools = await client.list_tools()
        except Exception as e:
            logger.error(
                "Failed to list tools from MCP server",
                extra={"server_name": server_name, "error": str(e)},
            )
            raise McpDiscoveryError(
                f"Failed to discover tools from server '{server_name}': {e}"
            ) from e

        # Handle empty tool list
        if not mcp_tools:
            logger.info(
                "MCP server has no tools",
                extra={"server_name": server_name, "tool_count": 0},
            )
            return []

        # Map each MCP tool to ToolDefinition + adapter
        results: list[tuple[ToolDefinition, McpToolExecutionAdapter]] = []
        for mcp_tool in mcp_tools:
            try:
                tool_def, adapter = McpToolDiscovery._map_tool(
                    mcp_tool,
                    server_name,
                    client,
                    settings=settings,
                    guardrail_engine=guardrail_engine,
                    audit_logger=audit_logger,
                )
                results.append((tool_def, adapter))
            except Exception as e:
                logger.warning(
                    "Failed to map MCP tool, skipping",
                    extra={
                        "server_name": server_name,
                        "tool_name": mcp_tool.get("name"),
                        "error": str(e),
                    },
                )
                continue

        logger.info(
            "MCP tool discovery complete",
            extra={
                "server_name": server_name,
                "tool_count": len(results),
                "mcp_discovery_tool_count": len(results),
            },
        )

        return results

    @staticmethod
    async def _validate_capabilities(client: McpClient, server_name: str) -> None:
        """Validate that MCP server supports required capabilities.

        Args:
            client: Connected MCP client instance.
            server_name: MCP server name for error messages.

        Raises:
            McpDiscoveryError: If required capabilities are missing.
        """
        # Note: For now, we assume that if the client can connect and has
        # list_tools() and call_tool() methods, the server supports the required
        # capabilities. A more complete implementation would involve checking
        # the server's advertised capabilities during the initialize handshake.
        # This is simplified for Phase 4; full capability negotiation can be
        # added in a future phase if needed.

        # Basic validation: ensure client has required methods
        if not hasattr(client, "list_tools") or not hasattr(client, "call_tool"):
            raise McpDiscoveryError(
                f"MCP server '{server_name}' client missing required methods "
                f"(list_tools, call_tool)"
            )

        logger.debug(
            "MCP server capabilities validated",
            extra={"server_name": server_name},
        )

    @staticmethod
    def _map_tool(
        mcp_tool: dict[str, Any],
        server_name: str,
        client: McpClient,
        *,
        settings: Settings | None = None,
        guardrail_engine: GuardrailEngine | None = None,
        audit_logger: AuditLogger | None = None,
    ) -> tuple[ToolDefinition, McpToolExecutionAdapter]:
        """Map a single MCP tool schema to ToolDefinition + adapter.

        Args:
            mcp_tool: MCP tool schema dict (name, description, inputSchema).
            server_name: MCP server name (for prefixing).
            client: MCP client instance (for adapter construction).

        Returns:
            Tuple of (ToolDefinition, McpToolExecutionAdapter).

        Raises:
            ValueError: If MCP tool schema is invalid.
        """
        # Extract required fields
        original_name = mcp_tool.get("name")
        if not original_name:
            raise ValueError("MCP tool missing required 'name' field")

        description = mcp_tool.get("description", "")
        input_schema = mcp_tool.get("inputSchema", {})

        # Prefix tool name with server name to prevent collisions
        prefixed_name = f"{server_name}.{original_name}"

        # Map MCP inputSchema → OpenAI function-calling parameters format
        # MCP inputSchema is already JSON Schema, but we ensure it has the required structure
        parameters = McpToolDiscovery._normalize_input_schema(input_schema)

        # Preserve tool origin metadata
        metadata = {
            "source": "mcp",
            "server_name": server_name,
            "transport": "stdio",
            "original_name": original_name,
        }

        # Create ToolDefinition
        tool_def = ToolDefinition(
            name=prefixed_name,
            description=description,
            parameters=parameters,
        )

        # Import here to avoid circular imports (executor imports discovery in tests)
        from app.ai.mcp.executor import McpToolExecutionAdapter

        # Instantiate adapter (Phase 5 implementation)
        adapter = McpToolExecutionAdapter(
            server_name=server_name,
            tool_name=original_name,
            client=client,
            metadata=metadata,
            settings=settings,
            guardrail_engine=guardrail_engine,
            audit_logger=audit_logger,
        )

        logger.debug(
            "Mapped MCP tool",
            extra={
                "server_name": server_name,
                "original_name": original_name,
                "prefixed_name": prefixed_name,
            },
        )

        return (tool_def, adapter)

    @staticmethod
    def _normalize_input_schema(input_schema: dict[str, Any]) -> dict[str, Any]:
        """Normalize MCP inputSchema to OpenAI function-calling parameters format.

        MCP uses JSON Schema for inputSchema. OpenAI function-calling expects
        JSON Schema as well, but with specific conventions (type: object, properties, etc.).

        Args:
            input_schema: MCP tool inputSchema (JSON Schema).

        Returns:
            Normalized parameters dict for ToolDefinition.
        """
        # If input_schema is empty or not a dict, return minimal valid schema
        if not input_schema or not isinstance(input_schema, dict):
            return {
                "type": "object",
                "properties": {},
                "required": [],
            }

        # Ensure it has at least a type field (default to object)
        normalized = dict(input_schema)
        if "type" not in normalized:
            normalized["type"] = "object"

        # Ensure properties exists for object types
        if normalized["type"] == "object" and "properties" not in normalized:
            normalized["properties"] = {}

        return normalized
