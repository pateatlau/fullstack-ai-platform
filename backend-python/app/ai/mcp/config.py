"""MCP connection configuration models."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, Literal, Sequence

from pydantic import BaseModel, Field, field_validator, model_validator

from app.ai.mcp.auth import McpServerCredentials


class McpConnectionConfig(BaseModel):
    """MCP server connection configuration (immutable after startup).

    Specifies the command, arguments, environment variables, and transport
    type needed to spawn and connect to an MCP server subprocess.

    Deep immutability: All nested containers (args, env) are converted to
    immutable types (tuple, MappingProxyType) to prevent mutation after
    model creation. Input compatibility preserved (accepts list/dict).
    """

    name: str = Field(..., description="Server identifier (must be unique)")
    command: str = Field(..., description="Executable command to spawn server")
    args: Sequence[str] = Field(
        default_factory=tuple,
        description="Command arguments (converted to immutable tuple)",
    )
    env: dict[str, str] = Field(
        default_factory=dict,
        description="Additional environment variables (immutable after validation)",
    )
    transport: Literal["stdio"] = Field(
        default="stdio", description="Transport type (stdio only in Phase 1)"
    )
    credentials: McpServerCredentials | None = Field(
        default=None,
        description="Optional server credentials (env vars, API keys, auth args)",
    )

    model_config = {"frozen": True}

    @field_validator("args", mode="before")
    @classmethod
    def _convert_args_to_tuple(cls, value: Any) -> tuple[str, ...]:
        """Convert list input to immutable tuple."""
        if isinstance(value, (list, tuple)):
            return tuple(value)
        return value

    @model_validator(mode="after")
    def _freeze_env_dict(self) -> "McpConnectionConfig":
        """Convert env dict to immutable MappingProxyType after validation."""
        if isinstance(self.env, dict):
            object.__setattr__(self, "env", MappingProxyType(self.env))
        return self


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
