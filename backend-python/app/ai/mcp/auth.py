"""MCP server credentials and authentication models."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, Sequence

from pydantic import (
    BaseModel,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)


class McpServerCredentials(BaseModel):
    """MCP server credentials (env-backed; immutable after resolution).

    Credentials are resolved from environment variables at startup and remain
    static for the process lifetime. No secret vault or dynamic rotation.

    Security: Sensitive fields (env_vars, api_keys) are excluded from repr()
    and masked in model_dump() to prevent credential leakage in logs or
    serialization. Direct attribute access remains available for runtime use.

    Deep immutability: All nested containers (env_vars, api_keys, command_args)
    are converted to immutable types (MappingProxyType, tuple) to prevent
    mutation after model creation. Input compatibility preserved (accepts dict/list).
    """

    env_vars: dict[str, str] = Field(
        default_factory=dict,
        description="Environment variables passed to MCP server subprocess (immutable after validation)",
        repr=False,
    )
    api_keys: dict[str, str] = Field(
        default_factory=dict,
        description="API keys (resolved from env; not logged by default) (immutable after validation)",
        repr=False,
    )
    command_args: Sequence[str] = Field(
        default_factory=tuple,
        description="Optional additional command arguments for auth (converted to immutable tuple)",
        repr=False,
    )

    model_config = {"frozen": True}

    @field_validator("command_args", mode="before")
    @classmethod
    def _convert_args_to_tuple(cls, value: Any) -> tuple[str, ...]:
        """Convert list input to immutable tuple."""
        if isinstance(value, (list, tuple)):
            return tuple(value)
        return value

    @model_validator(mode="after")
    def _freeze_dict_fields(self) -> "McpServerCredentials":
        """Convert dict fields to immutable MappingProxyType after validation."""
        if isinstance(self.env_vars, dict):
            object.__setattr__(self, "env_vars", MappingProxyType(self.env_vars))
        if isinstance(self.api_keys, dict):
            object.__setattr__(self, "api_keys", MappingProxyType(self.api_keys))
        return self

    @model_serializer
    def _serialize_model(self) -> dict[str, Any]:
        """Mask sensitive credential values in serialized output.

        Returns dict with masked values for env_vars, api_keys, and
        command_args to prevent credential leakage in logs or API responses.
        """
        return {
            "env_vars": {k: "***REDACTED***" for k in self.env_vars},
            "api_keys": {k: "***REDACTED***" for k in self.api_keys},
            "command_args": tuple("***REDACTED***" for _ in self.command_args),
        }
