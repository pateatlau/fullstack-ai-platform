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
        assert config.args == []
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
        assert config.args == [
            "-y",
            "@modelcontextprotocol/server-filesystem",
            "/tmp/workspace",
        ]
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


class TestMcpServerCredentials:
    """Test suite for McpServerCredentials model."""

    def test_empty_credentials(self) -> None:
        """Empty credentials (no env vars, api keys, or args)."""
        creds = McpServerCredentials()

        assert creds.env_vars == {}
        assert creds.api_keys == {}
        assert creds.command_args == []

    def test_env_vars_only(self) -> None:
        """Credentials with environment variables."""
        creds = McpServerCredentials(
            env_vars={"GITHUB_TOKEN": "ghp_abc123", "DEBUG": "true"}
        )

        assert creds.env_vars == {"GITHUB_TOKEN": "ghp_abc123", "DEBUG": "true"}
        assert creds.api_keys == {}
        assert creds.command_args == []

    def test_api_keys_only(self) -> None:
        """Credentials with API keys."""
        creds = McpServerCredentials(api_keys={"api_key": "sk-test-key"})

        assert creds.env_vars == {}
        assert creds.api_keys == {"api_key": "sk-test-key"}
        assert creds.command_args == []

    def test_full_credentials(self) -> None:
        """Credentials with all fields populated."""
        creds = McpServerCredentials(
            env_vars={"TOKEN": "tok123"},
            api_keys={"key": "val"},
            command_args=["--auth", "token"],
        )

        assert creds.env_vars == {"TOKEN": "tok123"}
        assert creds.api_keys == {"key": "val"}
        assert creds.command_args == ["--auth", "token"]

    def test_credentials_immutability(self) -> None:
        """Credentials are frozen after creation (immutable)."""
        creds = McpServerCredentials(env_vars={"KEY": "value"})

        with pytest.raises(ValidationError):
            creds.env_vars = {}  # type: ignore[misc]


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
