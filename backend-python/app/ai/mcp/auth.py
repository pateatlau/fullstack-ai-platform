"""MCP server credentials and authentication models."""

from __future__ import annotations

import re
from types import MappingProxyType
from typing import Any, Sequence

from pydantic import (
    BaseModel,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)

from app.ai.mcp.exceptions import McpAuthenticationError
from app.ai.security.secrets.resolver import EnvSecretResolver, SecretResolver


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


def resolve_credential_env_vars(
    env_vars: dict[str, str],
    allow_missing: bool = False,
    *,
    resolver: SecretResolver | None = None,
) -> dict[str, str]:
    """Resolve environment variable placeholders in credential values.

    Interpolates ${VAR_NAME} placeholders with secret values resolved via
    ``resolver``. Supports nested placeholders and validates all required
    vars exist.

    Args:
        env_vars: Dict with values potentially containing ${VAR_NAME} placeholders.
        allow_missing: If False (default), raise error on missing env vars;
                      if True, preserve placeholder for missing vars.
        resolver: ``SecretResolver`` to resolve placeholder names against.
                  Defaults to ``EnvSecretResolver`` (byte-for-byte today's
                  direct ``os.environ`` behaviour) — the vault swap point.

    Returns:
        Dict with all placeholders replaced by resolved secret values.

    Raises:
        McpAuthenticationError: If a required secret is missing and
                               allow_missing=False.

    Examples:
        >>> os.environ["GITHUB_TOKEN"] = "ghp_abc123"
        >>> resolve_credential_env_vars({"TOKEN": "${GITHUB_TOKEN}"})
        {'TOKEN': 'ghp_abc123'}

        >>> resolve_credential_env_vars(
        ...     {"PATH": "/home/${USER}/bin"},
        ...     allow_missing=False
        ... )
        {'PATH': '/home/john/bin'}  # If USER=john in env

        >>> resolve_credential_env_vars(
        ...     {"KEY": "${MISSING_VAR}"},
        ...     allow_missing=True
        ... )
        {'KEY': '${MISSING_VAR}'}  # Placeholder preserved
    """
    resolver = resolver or EnvSecretResolver()

    # Pattern to match ${VAR_NAME} placeholders
    env_var_pattern = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

    resolved: dict[str, str] = {}

    for key, value in env_vars.items():
        if not isinstance(value, str):
            resolved[key] = value
            continue

        # Find all placeholders in this value
        matches = env_var_pattern.findall(value)

        if not matches:
            # No placeholders, use value as-is
            resolved[key] = value
            continue

        # Replace each placeholder
        resolved_value = value
        for env_var_name in matches:
            placeholder = f"${{{env_var_name}}}"
            env_value = resolver.resolve(env_var_name)

            if env_value is None:
                if allow_missing:
                    # Preserve placeholder for missing vars
                    continue
                else:
                    raise McpAuthenticationError(
                        f"Missing required environment variable '{env_var_name}' "
                        f"referenced in credential field '{key}'",
                        missing_key=env_var_name,
                    )

            resolved_value = resolved_value.replace(placeholder, env_value)

        resolved[key] = resolved_value

    return resolved
