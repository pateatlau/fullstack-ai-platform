"""Register production tools on the application-scoped registry."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.ai.tools.implementations.web_search import (
    WEB_SEARCH_TOOL_DEFINITION,
    WebSearchClient,
    create_web_search_handler,
)
from app.ai.tools.registry import ToolRegistry
from app.core.config import Settings

if TYPE_CHECKING:
    from app.ai.mcp.config import McpConnectionConfig
    from app.ai.mcp.permissions import McpPermissionPolicy
    from app.ai.mcp.registry import McpServerRegistry

logger = logging.getLogger(__name__)


def register_production_tools(
    registry: ToolRegistry,
    settings: Settings,
    *,
    web_search_client: WebSearchClient | None = None,
) -> None:
    """Register V1 production tools when ``tools_enabled`` is true."""
    if settings.web_search_provider != "tavily":
        raise ValueError(
            f"Unsupported WEB_SEARCH_PROVIDER '{settings.web_search_provider}'. "
            "Supported providers: tavily."
        )

    handler = create_web_search_handler(settings=settings, client=web_search_client)
    registry.register(WEB_SEARCH_TOOL_DEFINITION, handler)


async def register_mcp_tools(
    registry: ToolRegistry,
    mcp_registry: "McpServerRegistry",
    settings: Settings,
    permission_policy: "McpPermissionPolicy | None" = None,
) -> None:
    """Register MCP tools from configured servers (Phase 8).

    Loads MCP server configs from settings, registers each server with
    McpServerRegistry, discovers tools, validates permissions, and registers
    approved tools with ToolRegistry.

    Connection/discovery errors are logged as warnings and do not crash startup.
    Servers missing required capabilities are skipped gracefully.

    Args:
        registry: ToolRegistry for registering discovered tools.
        mcp_registry: McpServerRegistry for server lifecycle management.
        settings: Application settings with mcp_servers config.
        permission_policy: Optional McpPermissionPolicy for per-server/per-tool auth.
    """
    from app.ai.mcp import MCP_SPEC_VERSION
    from app.ai.mcp.discovery import McpToolDiscovery
    from app.ai.mcp.exceptions import McpConnectionError, McpDiscoveryError
    from app.ai.mcp.permissions import McpPermissionPolicy

    # Create permission policy if not provided
    if permission_policy is None:
        permission_policy = McpPermissionPolicy(config=settings.mcp_permission_policy)

    # Early return if MCP is disabled
    if not settings.mcp_enabled:
        logger.debug("MCP integration disabled; skipping MCP tool registration")
        return

    if not settings.mcp_servers:
        logger.info("No MCP servers configured; skipping MCP tool registration")
        return

    logger.info(
        "Starting MCP tool registration",
        extra={
            "mcp_enabled": settings.mcp_enabled,
            "mcp_spec_version": MCP_SPEC_VERSION,
            "server_count": len(settings.mcp_servers),
        },
    )

    # Cache for discovered tools (no background rediscovery)
    discovery_cache: dict[str, list[tuple[Any, Any]]] = {}
    registered_tool_count = 0
    failed_server_count = 0
    skipped_tool_count = 0

    for server_config_dict in settings.mcp_servers:
        try:
            # Parse McpConnectionConfig from dict (immutable after startup)
            config = _parse_server_config(server_config_dict)
            server_name = config.name

            # Check server-level permission
            server_auth_error = permission_policy.authorize_server(server_name)
            if server_auth_error:
                logger.warning(
                    "MCP server denied by permission policy; skipping",
                    extra={
                        "server_name": server_name,
                        "error": server_auth_error,
                        "mcp_permission_denied": True,
                    },
                )
                skipped_tool_count += 1
                continue

            # Register server with McpServerRegistry (handles connection)
            try:
                await mcp_registry.register(server_name, config)
            except (McpConnectionError, ValueError) as exc:
                logger.warning(
                    "Failed to register MCP server; skipping",
                    extra={
                        "server_name": server_name,
                        "error": str(exc),
                        "mcp_connection_failed": True,
                    },
                )
                failed_server_count += 1
                continue

            # Get connected client
            client = mcp_registry.get(server_name)
            if not client:
                logger.warning(
                    "MCP server not available after registration; skipping",
                    extra={"server_name": server_name},
                )
                # Cleanup: unregister server to avoid resource leak
                await mcp_registry.unregister(server_name)
                failed_server_count += 1
                continue

            # Discover tools (validates capabilities internally)
            try:
                discovered_tools = await McpToolDiscovery.discover(client, server_name)
                discovery_cache[server_name] = discovered_tools
                logger.info(
                    "MCP tool discovery succeeded",
                    extra={
                        "server_name": server_name,
                        "tool_count": len(discovered_tools),
                        "mcp_discovery_tool_count": len(discovered_tools),
                        "mcp_discovery_cached": False,
                    },
                )
            except McpDiscoveryError as exc:
                logger.warning(
                    "MCP tool discovery failed; skipping server",
                    extra={
                        "server_name": server_name,
                        "error": str(exc),
                        "mcp_capability_missing": True,
                    },
                )
                # Cleanup: unregister server to disconnect and release resources
                await mcp_registry.unregister(server_name)
                failed_server_count += 1
                continue

            # Register each discovered tool with permission check
            for tool_def, adapter in discovered_tools:
                # Extract original tool name from metadata
                original_name = adapter.metadata.get("original_name", tool_def.name)

                # Check tool-level permission
                tool_auth_error = permission_policy.authorize_tool(
                    server_name, original_name
                )
                if tool_auth_error:
                    logger.warning(
                        "MCP tool denied by permission policy; skipping",
                        extra={
                            "server_name": server_name,
                            "tool_name": original_name,
                            "prefixed_name": tool_def.name,
                            "error": tool_auth_error,
                            "mcp_permission_denied": True,
                        },
                    )
                    skipped_tool_count += 1
                    continue

                # Register tool in ToolRegistry with error handling
                try:
                    registry.register(tool_def, adapter)
                    registered_tool_count += 1
                    logger.debug(
                        "Registered MCP tool",
                        extra={
                            "server_name": server_name,
                            "tool_name": original_name,
                            "prefixed_name": tool_def.name,
                            "tool_source": "mcp",
                        },
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to register MCP tool; skipping",
                        extra={
                            "server_name": server_name,
                            "tool_name": original_name,
                            "prefixed_name": tool_def.name,
                            "error": str(exc),
                        },
                    )
                    skipped_tool_count += 1
                    continue

        except Exception as exc:
            # Catch-all for unexpected errors during server processing
            server_name_fallback = server_config_dict.get("name", "<unknown>")
            logger.error(
                "Unexpected error during MCP server registration; skipping",
                extra={
                    "server_name": server_name_fallback,
                    "error": str(exc),
                },
                exc_info=True,
            )
            failed_server_count += 1
            continue

    logger.info(
        "MCP tool registration complete",
        extra={
            "registered_tool_count": registered_tool_count,
            "failed_server_count": failed_server_count,
            "skipped_tool_count": skipped_tool_count,
            "cached_server_count": len(discovery_cache),
        },
    )


def _parse_server_config(config_dict: dict[str, Any]) -> "McpConnectionConfig":
    """Parse MCP server config dict to McpConnectionConfig model.

    Args:
        config_dict: Raw config dict from settings.mcp_servers.

    Returns:
        Validated McpConnectionConfig instance.

    Raises:
        ValueError: If config is invalid or missing required fields.
    """
    from pydantic import ValidationError

    from app.ai.mcp.config import McpConnectionConfig

    server_name = config_dict.get("name", "<unknown>")

    try:
        return McpConnectionConfig(**config_dict)
    except ValidationError as exc:
        # Sanitize error: extract field locations without exposing values
        error_fields = [
            err["loc"][0] if err.get("loc") else "unknown" for err in exc.errors()
        ]
        error_summary = ", ".join(set(str(field) for field in error_fields))
        raise ValueError(
            f"Invalid MCP server config for '{server_name}': validation failed for fields: {error_summary}"
        ) from exc
    except Exception as exc:
        # Generic error case (non-validation errors)
        raise ValueError(
            f"Invalid MCP server config for '{server_name}': {type(exc).__name__}"
        ) from exc
