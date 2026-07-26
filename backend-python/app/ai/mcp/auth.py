"""MCP server credentials and authentication models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class McpServerCredentials(BaseModel):
    """MCP server credentials (env-backed; immutable after resolution).

    Credentials are resolved from environment variables at startup and remain
    static for the process lifetime. No secret vault or dynamic rotation.
    """

    env_vars: dict[str, str] = Field(
        default_factory=dict,
        description="Environment variables passed to MCP server subprocess",
    )
    api_keys: dict[str, str] = Field(
        default_factory=dict,
        description="API keys (resolved from env; not logged by default)",
    )
    command_args: list[str] = Field(
        default_factory=list,
        description="Optional additional command arguments for auth",
    )

    model_config = {"frozen": True}
