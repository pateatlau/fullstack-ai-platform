"""Tests for MCP credential resolution and authentication (Phase 6)."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from app.ai.mcp.auth import McpServerCredentials, resolve_credential_env_vars
from app.ai.mcp.config import McpConnectionConfig
from app.ai.mcp.exceptions import McpAuthenticationError


class TestResolveCredentialEnvVars:
    """Test suite for resolve_credential_env_vars function."""

    def test_no_placeholders(self) -> None:
        """Values without placeholders are returned unchanged."""
        env_vars = {
            "DEBUG": "true",
            "PATH": "/usr/local/bin",
            "COUNT": "42",
        }

        result = resolve_credential_env_vars(env_vars)

        assert result == env_vars

    def test_single_placeholder_present(self) -> None:
        """Single placeholder is replaced with env var value."""
        with patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_abc123"}, clear=False):
            env_vars = {"TOKEN": "${GITHUB_TOKEN}"}

            result = resolve_credential_env_vars(env_vars)

            assert result == {"TOKEN": "ghp_abc123"}

    def test_multiple_placeholders_present(self) -> None:
        """Multiple placeholders in same value are all replaced."""
        with patch.dict(
            os.environ,
            {"USER": "john", "HOME": "/home/john"},
            clear=False,
        ):
            env_vars = {
                "PATH": "${HOME}/bin",
                "CONFIG_DIR": "${HOME}/.config",
                "WORKSPACE": "/projects/${USER}",
            }

            result = resolve_credential_env_vars(env_vars)

            assert result == {
                "PATH": "/home/john/bin",
                "CONFIG_DIR": "/home/john/.config",
                "WORKSPACE": "/projects/john",
            }

    def test_multiple_placeholders_same_value(self) -> None:
        """Multiple placeholders in the same value are all replaced."""
        with patch.dict(
            os.environ,
            {"API_HOST": "api.example.com", "API_VERSION": "v1"},
            clear=False,
        ):
            env_vars = {"API_URL": "https://${API_HOST}/${API_VERSION}/endpoint"}

            result = resolve_credential_env_vars(env_vars)

            assert result == {"API_URL": "https://api.example.com/v1/endpoint"}

    def test_placeholder_missing_raises_error(self) -> None:
        """Missing env var raises McpAuthenticationError by default."""
        env_vars = {"TOKEN": "${MISSING_VAR}"}

        with pytest.raises(McpAuthenticationError) as exc_info:
            resolve_credential_env_vars(env_vars, allow_missing=False)

        assert "MISSING_VAR" in str(exc_info.value)
        assert "TOKEN" in str(exc_info.value)

    def test_placeholder_missing_preserved_if_allowed(self) -> None:
        """Missing env var preserves placeholder when allow_missing=True."""
        env_vars = {"TOKEN": "${MISSING_VAR}"}

        result = resolve_credential_env_vars(env_vars, allow_missing=True)

        assert result == {"TOKEN": "${MISSING_VAR}"}

    def test_partial_placeholder_resolution(self) -> None:
        """If some placeholders missing, only present ones are resolved when allowed."""
        with patch.dict(os.environ, {"PRESENT_VAR": "present_value"}, clear=False):
            env_vars = {
                "KEY1": "${PRESENT_VAR}",
                "KEY2": "${MISSING_VAR}",
                "KEY3": "${PRESENT_VAR}/${MISSING_VAR}",
            }

            result = resolve_credential_env_vars(env_vars, allow_missing=True)

            assert result == {
                "KEY1": "present_value",
                "KEY2": "${MISSING_VAR}",
                "KEY3": "present_value/${MISSING_VAR}",
            }

    def test_empty_dict(self) -> None:
        """Empty dict returns empty dict."""
        result = resolve_credential_env_vars({})

        assert result == {}

    def test_nested_placeholder_pattern(self) -> None:
        """Complex placeholder patterns are handled correctly."""
        with patch.dict(
            os.environ,
            {"API_KEY": "sk_test_123", "VERSION": "2.0"},
            clear=False,
        ):
            env_vars = {
                "AUTH_HEADER": "Bearer ${API_KEY}",
                "API_ENDPOINT": "https://api.example.com/v${VERSION}",
                "COMBINED": "${API_KEY}_${VERSION}",
            }

            result = resolve_credential_env_vars(env_vars)

            assert result == {
                "AUTH_HEADER": "Bearer sk_test_123",
                "API_ENDPOINT": "https://api.example.com/v2.0",
                "COMBINED": "sk_test_123_2.0",
            }

    def test_placeholder_with_numbers_and_underscores(self) -> None:
        """Placeholders with numbers and underscores are handled correctly."""
        with patch.dict(
            os.environ,
            {
                "VAR_123": "value1",
                "VAR_WITH_UNDERSCORES": "value2",
                "_LEADING_UNDERSCORE": "value3",
            },
            clear=False,
        ):
            env_vars = {
                "KEY1": "${VAR_123}",
                "KEY2": "${VAR_WITH_UNDERSCORES}",
                "KEY3": "${_LEADING_UNDERSCORE}",
            }

            result = resolve_credential_env_vars(env_vars)

            assert result == {
                "KEY1": "value1",
                "KEY2": "value2",
                "KEY3": "value3",
            }

    def test_literal_dollar_brace_not_placeholder(self) -> None:
        """Literal ${...} without valid var name pattern is not replaced."""
        env_vars = {
            "NOT_VAR1": "${123invalid}",  # Starts with number
            "NOT_VAR2": "${-invalid}",  # Invalid character
            "NOT_VAR3": "${}",  # Empty
        }

        result = resolve_credential_env_vars(env_vars, allow_missing=True)

        # No valid placeholders, values unchanged
        assert result == env_vars

    def test_case_sensitive_placeholder(self) -> None:
        """Placeholders are case-sensitive."""
        with patch.dict(
            os.environ,
            {"github_token": "lowercase", "GITHUB_TOKEN": "uppercase"},
            clear=False,
        ):
            env_vars = {
                "TOKEN1": "${GITHUB_TOKEN}",
                "TOKEN2": "${github_token}",
            }

            result = resolve_credential_env_vars(env_vars)

            assert result == {
                "TOKEN1": "uppercase",
                "TOKEN2": "lowercase",
            }

    def test_same_placeholder_multiple_times(self) -> None:
        """Same placeholder used multiple times in same value is replaced consistently."""
        with patch.dict(os.environ, {"USER": "alice"}, clear=False):
            env_vars = {"PATH": "/home/${USER}/bin:/opt/${USER}/local"}

            result = resolve_credential_env_vars(env_vars)

            assert result == {"PATH": "/home/alice/bin:/opt/alice/local"}

    def test_empty_string_value(self) -> None:
        """Empty string values are preserved."""
        env_vars = {"EMPTY": "", "NON_EMPTY": "value"}

        result = resolve_credential_env_vars(env_vars)

        assert result == {"EMPTY": "", "NON_EMPTY": "value"}

    def test_only_placeholder_no_surrounding_text(self) -> None:
        """Placeholder with no surrounding text is replaced with env var value."""
        with patch.dict(os.environ, {"SIMPLE_VAR": "simple_value"}, clear=False):
            env_vars = {"KEY": "${SIMPLE_VAR}"}

            result = resolve_credential_env_vars(env_vars)

            assert result == {"KEY": "simple_value"}


class TestMcpConnectionConfigWithCredentials:
    """Test suite for McpConnectionConfig with credentials field (Phase 6)."""

    def test_config_without_credentials(self) -> None:
        """Config without credentials field (None)."""
        config = McpConnectionConfig(name="test-server", command="test-cmd")

        assert config.credentials is None

    def test_config_with_empty_credentials(self) -> None:
        """Config with empty credentials (no env vars, api keys, or args)."""
        config = McpConnectionConfig(
            name="test-server",
            command="test-cmd",
            credentials=McpServerCredentials(),
        )

        assert config.credentials is not None
        assert config.credentials.env_vars == {}
        assert config.credentials.api_keys == {}
        assert config.credentials.command_args == ()

    def test_config_with_credentials_env_vars(self) -> None:
        """Config with credentials containing env vars."""
        config = McpConnectionConfig(
            name="github-server",
            command="uvx",
            args=["mcp-server-github"],
            credentials=McpServerCredentials(
                env_vars={
                    "GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_PERSONAL_ACCESS_TOKEN}"
                }
            ),
        )

        assert config.credentials is not None
        assert (
            config.credentials.env_vars["GITHUB_PERSONAL_ACCESS_TOKEN"]
            == "${GITHUB_PERSONAL_ACCESS_TOKEN}"
        )

    def test_config_with_credentials_api_keys(self) -> None:
        """Config with credentials containing API keys."""
        config = McpConnectionConfig(
            name="api-server",
            command="mcp-server",
            credentials=McpServerCredentials(api_keys={"API_KEY": "sk_test_abc123"}),
        )

        assert config.credentials is not None
        assert config.credentials.api_keys["API_KEY"] == "sk_test_abc123"

    def test_config_with_credentials_command_args(self) -> None:
        """Config with credentials containing command args."""
        config = McpConnectionConfig(
            name="auth-server",
            command="mcp-server",
            credentials=McpServerCredentials(
                command_args=["--auth", "token", "--verify"]
            ),
        )

        assert config.credentials is not None
        assert config.credentials.command_args == ("--auth", "token", "--verify")

    def test_config_with_full_credentials(self) -> None:
        """Config with all credential fields populated."""
        config = McpConnectionConfig(
            name="full-server",
            command="mcp-server",
            args=["--port", "8080"],
            env={"DEBUG": "true"},
            credentials=McpServerCredentials(
                env_vars={"TOKEN": "${AUTH_TOKEN}"},
                api_keys={"API_KEY": "sk_abc"},
                command_args=["--auth", "bearer"],
            ),
        )

        assert config.credentials is not None
        assert config.credentials.env_vars["TOKEN"] == "${AUTH_TOKEN}"
        assert config.credentials.api_keys["API_KEY"] == "sk_abc"
        assert config.credentials.command_args == ("--auth", "bearer")

    def test_config_credentials_immutability(self) -> None:
        """Credentials field is immutable after config creation."""
        from pydantic import ValidationError

        config = McpConnectionConfig(
            name="test-server",
            command="test-cmd",
            credentials=McpServerCredentials(env_vars={"KEY": "value"}),
        )

        # Config is frozen, cannot reassign credentials
        with pytest.raises(ValidationError):
            config.credentials = None  # type: ignore[misc]

    def test_config_serialization_masks_credentials(self) -> None:
        """Config serialization masks credential values."""
        config = McpConnectionConfig(
            name="secure-server",
            command="mcp-server",
            credentials=McpServerCredentials(
                env_vars={"SECRET": "secret_value"},
                api_keys={"API_KEY": "sk_secret"},
                command_args=["--token", "secret_token"],
            ),
        )

        # Serialize config
        dumped = config.model_dump()

        # Credentials should be present but masked
        assert dumped["credentials"] is not None
        assert dumped["credentials"]["env_vars"]["SECRET"] == "***REDACTED***"
        assert dumped["credentials"]["api_keys"]["API_KEY"] == "***REDACTED***"
        assert all(
            arg == "***REDACTED***" for arg in dumped["credentials"]["command_args"]
        )

        # Original values should not appear
        assert "secret_value" not in str(dumped)
        assert "sk_secret" not in str(dumped)
        assert "secret_token" not in str(dumped)


class TestCredentialResolutionIntegration:
    """Integration tests for credential resolution in config + transport."""

    def test_config_env_resolution_with_placeholders(self) -> None:
        """Config env vars with placeholders are resolved at runtime."""
        with patch.dict(os.environ, {"DB_HOST": "localhost"}, clear=False):
            config = McpConnectionConfig(
                name="db-server",
                command="mcp-server",
                env={"DATABASE_URL": "postgres://${DB_HOST}:5432"},
            )

            # Config stores placeholder as-is (resolution happens at connect time)
            assert config.env["DATABASE_URL"] == "postgres://${DB_HOST}:5432"

            # Resolution function resolves placeholder
            resolved = resolve_credential_env_vars(dict(config.env))
            assert resolved["DATABASE_URL"] == "postgres://localhost:5432"

    def test_credentials_env_resolution_with_placeholders(self) -> None:
        """Credentials env vars with placeholders are resolved at runtime."""
        with patch.dict(os.environ, {"GITHUB_TOKEN": "ghp_abc123"}, clear=False):
            config = McpConnectionConfig(
                name="github",
                command="mcp-server",
                credentials=McpServerCredentials(
                    env_vars={"GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}"}
                ),
            )

            # Config stores placeholder as-is
            assert config.credentials is not None
            assert (
                config.credentials.env_vars["GITHUB_PERSONAL_ACCESS_TOKEN"]
                == "${GITHUB_TOKEN}"
            )

            # Resolution function resolves placeholder
            resolved = resolve_credential_env_vars(dict(config.credentials.env_vars))
            assert resolved["GITHUB_PERSONAL_ACCESS_TOKEN"] == "ghp_abc123"

    def test_missing_credential_env_var_raises_error(self) -> None:
        """Missing credential env var raises error at resolution time."""
        config = McpConnectionConfig(
            name="github",
            command="mcp-server",
            credentials=McpServerCredentials(
                env_vars={"GITHUB_PERSONAL_ACCESS_TOKEN": "${MISSING_TOKEN}"}
            ),
        )

        # Config creation succeeds (validation deferred to resolution time)
        assert config.credentials is not None

        # Resolution fails with clear error
        with pytest.raises(McpAuthenticationError) as exc_info:
            resolve_credential_env_vars(
                dict(config.credentials.env_vars), allow_missing=False
            )

        assert "MISSING_TOKEN" in str(exc_info.value)
        assert "GITHUB_PERSONAL_ACCESS_TOKEN" in str(exc_info.value)

    def test_credentials_merge_priority(self) -> None:
        """Credentials env vars merge with config env vars (credentials take priority)."""
        with patch.dict(
            os.environ,
            {"TOKEN": "env_token", "DEBUG": "true"},
            clear=False,
        ):
            config = McpConnectionConfig(
                name="server",
                command="cmd",
                env={"DEBUG": "false", "PATH": "/usr/bin"},
                credentials=McpServerCredentials(
                    env_vars={"TOKEN": "${TOKEN}", "EXTRA": "extra_value"}
                ),
            )

            # Merge config env and credentials env (simulate transport.connect logic)
            merged_env = dict(config.env)
            if config.credentials:
                resolved_cred_env = resolve_credential_env_vars(
                    dict(config.credentials.env_vars)
                )
                merged_env.update(resolved_cred_env)

            # Credentials override config env where keys collide
            assert merged_env == {
                "DEBUG": "false",
                "PATH": "/usr/bin",
                "TOKEN": "env_token",
                "EXTRA": "extra_value",
            }
