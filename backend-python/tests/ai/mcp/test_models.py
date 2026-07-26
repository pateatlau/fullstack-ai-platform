"""Tests for MCP configuration and credential models (Phase 1)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.ai.mcp.auth import McpServerCredentials
from app.ai.mcp.config import McpConnectionConfig, McpToolCall, McpToolResult


class TestMcpConnectionConfig:
    """Test suite for McpConnectionConfig model."""

    def test_minimal_config(self) -> None:
        """Minimal config with only name and command."""
        config = McpConnectionConfig(name="test-server", command="test-cmd")

        assert config.name == "test-server"
        assert config.command == "test-cmd"
        assert config.args == ()  # Empty tuple (immutable)
        assert config.env == {}
        assert config.transport == "stdio"

    def test_full_config(self) -> None:
        """Full config with all fields populated."""
        config = McpConnectionConfig(
            name="filesystem",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp/workspace"],
            env={"DEBUG": "true"},
            transport="stdio",
        )

        assert config.name == "filesystem"
        assert config.command == "npx"
        # args converted to tuple (immutable)
        assert config.args == (
            "-y",
            "@modelcontextprotocol/server-filesystem",
            "/tmp/workspace",
        )
        assert isinstance(config.args, tuple)
        # env converted to MappingProxyType (immutable)
        assert config.env == {"DEBUG": "true"}
        assert config.transport == "stdio"

    def test_config_immutability(self) -> None:
        """Config is frozen after creation (immutable)."""
        config = McpConnectionConfig(name="test", command="cmd")

        with pytest.raises(ValidationError):
            config.name = "modified"  # type: ignore[misc]

    def test_missing_required_fields(self) -> None:
        """ValidationError when required fields (name, command) are missing."""
        with pytest.raises(ValidationError):
            McpConnectionConfig()  # type: ignore[call-arg]

        with pytest.raises(ValidationError):
            McpConnectionConfig(name="test")  # type: ignore[call-arg]

        with pytest.raises(ValidationError):
            McpConnectionConfig(command="cmd")  # type: ignore[call-arg]

    def test_args_deep_immutability(self) -> None:
        """args field is deeply immutable (tuple, cannot be modified)."""
        config = McpConnectionConfig(name="test", command="cmd", args=["arg1", "arg2"])

        # Converted to tuple (immutable)
        assert isinstance(config.args, tuple)
        assert config.args == ("arg1", "arg2")

        # Mutation attempts fail
        with pytest.raises(AttributeError):
            config.args.append("arg3")  # type: ignore[attr-defined]

    def test_env_deep_immutability(self) -> None:
        """env field is deeply immutable (MappingProxyType, cannot be modified)."""
        config = McpConnectionConfig(name="test", command="cmd", env={"KEY": "value"})

        # env is MappingProxyType (read-only dict)
        assert config.env == {"KEY": "value"}
        assert config.env["KEY"] == "value"

        # Mutation attempts fail
        with pytest.raises(TypeError):
            config.env["NEW_KEY"] = "new_value"  # type: ignore[index]

        with pytest.raises(TypeError):
            config.env["KEY"] = "modified"  # type: ignore[index]

    def test_transport_validation_accepts_stdio(self) -> None:
        """transport field accepts 'stdio' (only supported value in Phase 1)."""
        config = McpConnectionConfig(name="test", command="cmd", transport="stdio")

        assert config.transport == "stdio"

    def test_transport_validation_rejects_invalid(self) -> None:
        """transport field rejects unsupported values."""
        # SSE transport not implemented yet (Phase 1 stdio only)
        with pytest.raises(ValidationError):
            McpConnectionConfig(name="test", command="cmd", transport="sse")  # type: ignore[arg-type]

        # Invalid transport
        with pytest.raises(ValidationError):
            McpConnectionConfig(name="test", command="cmd", transport="http")  # type: ignore[arg-type]

        # Empty string
        with pytest.raises(ValidationError):
            McpConnectionConfig(name="test", command="cmd", transport="")  # type: ignore[arg-type]


class TestMcpServerCredentials:
    """Test suite for McpServerCredentials model."""

    def test_empty_credentials(self) -> None:
        """Empty credentials (no env vars, api keys, or args)."""
        creds = McpServerCredentials()

        assert creds.env_vars == {}
        assert creds.api_keys == {}
        assert creds.command_args == ()  # Empty tuple (immutable)

    def test_env_vars_only(self) -> None:
        """Credentials with environment variables."""
        creds = McpServerCredentials(
            env_vars={"GITHUB_TOKEN": "ghp_abc123", "DEBUG": "true"}
        )

        assert creds.env_vars == {"GITHUB_TOKEN": "ghp_abc123", "DEBUG": "true"}
        assert creds.api_keys == {}
        assert creds.command_args == ()  # Empty tuple (immutable)

    def test_api_keys_only(self) -> None:
        """Credentials with API keys."""
        creds = McpServerCredentials(api_keys={"api_key": "sk-test-key"})

        assert creds.env_vars == {}
        assert creds.api_keys == {"api_key": "sk-test-key"}
        assert creds.command_args == ()  # Empty tuple (immutable)

    def test_full_credentials(self) -> None:
        """Credentials with all fields populated."""
        creds = McpServerCredentials(
            env_vars={"TOKEN": "tok123"},
            api_keys={"key": "val"},
            command_args=["--auth", "token"],
        )

        assert creds.env_vars == {"TOKEN": "tok123"}
        assert creds.api_keys == {"key": "val"}
        assert creds.command_args == ("--auth", "token")  # Tuple (immutable)

    def test_credentials_immutability(self) -> None:
        """Credentials are frozen after creation (immutable)."""
        creds = McpServerCredentials(env_vars={"KEY": "value"})

        with pytest.raises(ValidationError):
            creds.env_vars = {}  # type: ignore[misc]

    def test_credentials_repr_redacts_secrets(self) -> None:
        """repr() excludes sensitive credential fields."""
        creds = McpServerCredentials(
            env_vars={"GITHUB_TOKEN": "ghp_secret123"},
            api_keys={"api_key": "sk-secret456"},
            command_args=["--token", "secret789"],
        )

        repr_str = repr(creds)

        # Sensitive values should not appear in repr
        assert "ghp_secret123" not in repr_str
        assert "sk-secret456" not in repr_str
        assert "secret789" not in repr_str

    def test_credentials_model_dump_masks_secrets(self) -> None:
        """model_dump() masks sensitive credential values."""
        creds = McpServerCredentials(
            env_vars={"GITHUB_TOKEN": "ghp_secret123", "DEBUG": "true"},
            api_keys={"api_key": "sk-secret456", "other_key": "val"},
            command_args=["--token", "secret789"],
        )

        dumped = creds.model_dump()

        # All env_vars keys present but values masked
        assert "GITHUB_TOKEN" in dumped["env_vars"]
        assert "DEBUG" in dumped["env_vars"]
        assert dumped["env_vars"]["GITHUB_TOKEN"] == "***REDACTED***"
        assert dumped["env_vars"]["DEBUG"] == "***REDACTED***"

        # All api_keys keys present but values masked
        assert "api_key" in dumped["api_keys"]
        assert "other_key" in dumped["api_keys"]
        assert dumped["api_keys"]["api_key"] == "***REDACTED***"
        assert dumped["api_keys"]["other_key"] == "***REDACTED***"

        # command_args length preserved but values masked
        assert len(dumped["command_args"]) == 2
        assert all(arg == "***REDACTED***" for arg in dumped["command_args"])

    def test_credentials_direct_access_preserves_values(self) -> None:
        """Direct attribute access returns actual credential values."""
        creds = McpServerCredentials(
            env_vars={"GITHUB_TOKEN": "ghp_secret123"},
            api_keys={"api_key": "sk-secret456"},
            command_args=["--token", "secret789"],
        )

        # Direct access returns real values (needed for runtime use)
        assert creds.env_vars["GITHUB_TOKEN"] == "ghp_secret123"
        assert creds.api_keys["api_key"] == "sk-secret456"
        # command_args converted to tuple (immutable)
        assert creds.command_args == ("--token", "secret789")

    def test_credentials_env_vars_deep_immutability(self) -> None:
        """env_vars field is deeply immutable (MappingProxyType)."""
        creds = McpServerCredentials(env_vars={"TOKEN": "secret"})

        # Converted to MappingProxyType (read-only)
        assert creds.env_vars["TOKEN"] == "secret"

        # Mutation attempts fail
        with pytest.raises(TypeError):
            creds.env_vars["NEW_KEY"] = "value"  # type: ignore[index]

        with pytest.raises(TypeError):
            creds.env_vars["TOKEN"] = "modified"  # type: ignore[index]

    def test_credentials_api_keys_deep_immutability(self) -> None:
        """api_keys field is deeply immutable (MappingProxyType)."""
        creds = McpServerCredentials(api_keys={"key": "secret"})

        # Converted to MappingProxyType (read-only)
        assert creds.api_keys["key"] == "secret"

        # Mutation attempts fail
        with pytest.raises(TypeError):
            creds.api_keys["new_key"] = "value"  # type: ignore[index]

    def test_credentials_command_args_deep_immutability(self) -> None:
        """command_args field is deeply immutable (tuple)."""
        creds = McpServerCredentials(command_args=["--auth", "token"])

        # Converted to tuple (immutable)
        assert isinstance(creds.command_args, tuple)
        assert creds.command_args == ("--auth", "token")

        # Mutation attempts fail
        with pytest.raises(AttributeError):
            creds.command_args.append("new_arg")  # type: ignore[attr-defined]


class TestMcpToolCall:
    """Test suite for McpToolCall internal model."""

    def test_minimal_tool_call(self) -> None:
        """Minimal tool call with only name."""
        call = McpToolCall(name="read_file")

        assert call.name == "read_file"
        assert call.arguments == {}

    def test_full_tool_call(self) -> None:
        """Full tool call with arguments."""
        call = McpToolCall(name="read_file", arguments={"path": "/tmp/test.txt"})

        assert call.name == "read_file"
        assert call.arguments == {"path": "/tmp/test.txt"}

    def test_missing_name(self) -> None:
        """ValidationError when name is missing."""
        with pytest.raises(ValidationError):
            McpToolCall()  # type: ignore[call-arg]


class TestMcpToolResult:
    """Test suite for McpToolResult internal model."""

    def test_success_result(self) -> None:
        """Successful tool result."""
        result = McpToolResult(success=True, data={"content": "file contents"})

        assert result.success is True
        assert result.data == {"content": "file contents"}
        assert result.error is None

    def test_error_result(self) -> None:
        """Error tool result."""
        result = McpToolResult(success=False, error="File not found")

        assert result.success is False
        assert result.data is None
        assert result.error == "File not found"

    def test_minimal_result(self) -> None:
        """Minimal result with only success flag."""
        result = McpToolResult(success=True)

        assert result.success is True
        assert result.data is None
        assert result.error is None

    def test_missing_success_flag(self) -> None:
        """ValidationError when success flag is missing."""
        with pytest.raises(ValidationError):
            McpToolResult()  # type: ignore[call-arg]
