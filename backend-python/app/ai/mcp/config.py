"""MCP connection configuration models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class McpConnectionConfig(BaseModel):
    """MCP server connection configuration (immutable after startup).

    Specifies the command, arguments, environment variables, and transport
    type needed to spawn and connect to an MCP server subprocess.
    """

    name: str = Field(..., description="Server identifier (must be unique)")
    command: str = Field(..., description="Executable command to spawn server")
    args: list[str] = Field(default_factory=list, description="Command arguments")
    env: dict[str, str] = Field(
        default_factory=dict, description="Additional environment variables"
    )
    transport: str = Field(
        default="stdio", description="Transport type (stdio only in Phase 1)"
    )

    model_config = {"frozen": True}


class McpToolCall(BaseModel):
    """Internal: MCP tool invocation payload (not part of public API).

    Maps to MCP JSON-RPC tools/call request format.
    """

    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class McpToolResult(BaseModel):
    """Internal: MCP tool result envelope (not part of public API).

    Maps from MCP JSON-RPC tools/call response to ToolResult.
    """

    success: bool
    data: object | None = None
    error: str | None = None
