"""Tests for MCP permission policy (Phase 7).

Test coverage:
- McpPermissionPolicy initialization with various configs
- authorize_server: empty allowlist, present/absent servers
- authorize_tool: wildcard, specific tools, inheritance from server policy
- Integration with ToolAuthorizer: both must pass
- Guest denial inherited from ToolAuthorizer
"""

from __future__ import annotations

import uuid

import pytest

from app.ai.mcp.permissions import McpPermissionPolicy
from app.ai.tools.executor import ToolExecutor
from app.ai.tools.registry import ToolRegistry
from app.ai.tools.schemas import (
    ToolCall,
    ToolDefinition,
    ToolExecutionContext,
    ToolResult,
)
from app.core.caller import CallerContext
from app.core.config import Settings


class FakeMcpToolHandler:
    """Fake MCP tool handler with server_name and tool_name attributes."""

    def __init__(
        self, server_name: str, tool_name: str, result: ToolResult | None = None
    ):
        self.server_name = server_name
        self.tool_name = tool_name
        self._result = result or ToolResult(success=True, data="test result")

    async def execute(
        self, args: dict[str, object], context: ToolExecutionContext
    ) -> ToolResult:
        return self._result


class FakeLocalToolHandler:
    """Fake local (non-MCP) tool handler without MCP attributes."""

    async def execute(
        self, args: dict[str, object], context: ToolExecutionContext
    ) -> ToolResult:
        return ToolResult(success=True, data="local result")


# --- McpPermissionPolicy unit tests ---


def test_mcp_permission_policy_empty_config_allows_all():
    """Empty config → all servers and tools allowed."""
    policy = McpPermissionPolicy(config={})

    assert policy.authorize_server("filesystem") is None
    assert policy.authorize_server("github") is None
    assert policy.authorize_tool("filesystem", "read_file") is None
    assert policy.authorize_tool("github", "create_issue") is None


def test_mcp_permission_policy_none_config_allows_all():
    """None config → all servers and tools allowed."""
    policy = McpPermissionPolicy(config=None)

    assert policy.authorize_server("filesystem") is None
    assert policy.authorize_tool("filesystem", "read_file") is None


def test_mcp_permission_policy_allowed_servers_empty_allows_all():
    """Empty allowed_servers list → all servers allowed."""
    policy = McpPermissionPolicy(config={"allowed_servers": []})

    assert policy.authorize_server("filesystem") is None
    assert policy.authorize_server("github") is None


def test_mcp_permission_policy_allowed_servers_restricts():
    """Non-empty allowed_servers → only listed servers allowed."""
    policy = McpPermissionPolicy(
        config={
            "allowed_servers": ["filesystem", "github"],
        }
    )

    # Allowed servers pass
    assert policy.authorize_server("filesystem") is None
    assert policy.authorize_server("github") is None

    # Disallowed server fails
    error = policy.authorize_server("slack")
    assert error is not None
    assert "slack" in error
    assert "not in the allowed servers list" in error


def test_mcp_permission_policy_allowed_tools_empty_allows_all():
    """Empty allowed_tools dict → all tools from allowed servers allowed."""
    policy = McpPermissionPolicy(
        config={
            "allowed_servers": ["filesystem"],
            "allowed_tools": {},
        }
    )

    assert policy.authorize_tool("filesystem", "read_file") is None
    assert policy.authorize_tool("filesystem", "write_file") is None


def test_mcp_permission_policy_allowed_tools_wildcard():
    """allowed_tools with '*' → all tools from that server allowed."""
    policy = McpPermissionPolicy(
        config={
            "allowed_servers": ["filesystem"],
            "allowed_tools": {
                "filesystem": ["*"],
            },
        }
    )

    assert policy.authorize_tool("filesystem", "read_file") is None
    assert policy.authorize_tool("filesystem", "write_file") is None
    assert policy.authorize_tool("filesystem", "delete_file") is None


def test_mcp_permission_policy_allowed_tools_specific():
    """allowed_tools with specific tool names → only listed tools allowed."""
    policy = McpPermissionPolicy(
        config={
            "allowed_servers": ["filesystem"],
            "allowed_tools": {
                "filesystem": ["read_file", "list_directory"],
            },
        }
    )

    # Allowed tools pass
    assert policy.authorize_tool("filesystem", "read_file") is None
    assert policy.authorize_tool("filesystem", "list_directory") is None

    # Disallowed tool fails
    error = policy.authorize_tool("filesystem", "write_file")
    assert error is not None
    assert "write_file" in error
    assert "not in the allowed tools list" in error


def test_mcp_permission_policy_allowed_tools_missing_server_entry():
    """allowed_tools without entry for server → all tools from that server allowed."""
    policy = McpPermissionPolicy(
        config={
            "allowed_servers": ["filesystem", "github"],
            "allowed_tools": {
                "filesystem": ["read_file"],
            },
        }
    )

    # filesystem has restricted tools
    assert policy.authorize_tool("filesystem", "read_file") is None
    assert policy.authorize_tool("filesystem", "write_file") is not None

    # github has no entry → all tools allowed
    assert policy.authorize_tool("github", "create_issue") is None
    assert policy.authorize_tool("github", "list_repos") is None


def test_mcp_permission_policy_authorize_tool_checks_server_first():
    """authorize_tool checks server authorization first."""
    policy = McpPermissionPolicy(
        config={
            "allowed_servers": ["filesystem"],
            "allowed_tools": {
                "github": ["*"],  # github tools allowed IF server is allowed
            },
        }
    )

    # github server not allowed → tool denied even though tools list allows it
    error = policy.authorize_tool("github", "create_issue")
    assert error is not None
    assert "github" in error
    assert "not in the allowed servers list" in error


def test_mcp_permission_policy_multiple_servers_independent():
    """Multiple servers with different tool policies are independent."""
    policy = McpPermissionPolicy(
        config={
            "allowed_servers": ["filesystem", "github"],
            "allowed_tools": {
                "filesystem": ["read_file"],
                "github": ["*"],
            },
        }
    )

    # filesystem: restricted
    assert policy.authorize_tool("filesystem", "read_file") is None
    assert policy.authorize_tool("filesystem", "write_file") is not None

    # github: wildcard
    assert policy.authorize_tool("github", "create_issue") is None
    assert policy.authorize_tool("github", "list_repos") is None


# --- Integration tests with ToolExecutor ---


@pytest.mark.asyncio
async def test_tool_executor_mcp_permission_policy_allows_mcp_tool():
    """MCP tool passes both ToolAuthorizer and McpPermissionPolicy."""
    registry = ToolRegistry()
    settings = Settings()
    policy = McpPermissionPolicy(
        config={
            "allowed_servers": ["filesystem"],
            "allowed_tools": {"filesystem": ["read_file"]},
        }
    )

    executor = ToolExecutor(
        registry=registry,
        settings=settings,
        mcp_permission_policy=policy,
    )

    # Register MCP tool
    tool_def = ToolDefinition(
        name="filesystem.read_file",
        description="Read a file",
        parameters={"type": "object", "properties": {}},
    )
    handler = FakeMcpToolHandler(server_name="filesystem", tool_name="read_file")
    registry.register(tool_def, handler)

    # Authenticated user + allowed tool → success
    context = ToolExecutionContext(
        caller=CallerContext(kind="user", user_id=uuid.uuid4())
    )
    call = ToolCall(name="filesystem.read_file", arguments={})

    result = await executor.execute(call, context)
    assert result.success is True


@pytest.mark.asyncio
async def test_tool_executor_mcp_permission_policy_denies_mcp_tool():
    """MCP tool denied by McpPermissionPolicy even if ToolAuthorizer allows."""
    registry = ToolRegistry()
    settings = Settings()
    policy = McpPermissionPolicy(
        config={
            "allowed_servers": ["filesystem"],
            "allowed_tools": {
                "filesystem": ["list_directory"]
            },  # read_file not allowed
        }
    )

    executor = ToolExecutor(
        registry=registry,
        settings=settings,
        mcp_permission_policy=policy,
    )

    # Register MCP tool
    tool_def = ToolDefinition(
        name="filesystem.read_file",
        description="Read a file",
        parameters={"type": "object", "properties": {}},
    )
    handler = FakeMcpToolHandler(server_name="filesystem", tool_name="read_file")
    registry.register(tool_def, handler)

    # Authenticated user but tool not in allowlist → forbidden
    context = ToolExecutionContext(
        caller=CallerContext(kind="user", user_id=uuid.uuid4())
    )
    call = ToolCall(name="filesystem.read_file", arguments={})

    result = await executor.execute(call, context)
    assert result.success is False
    assert result.error_code == "forbidden"
    assert result.error is not None and "read_file" in result.error
    assert result.error is not None and "not in the allowed tools list" in result.error


@pytest.mark.asyncio
async def test_tool_executor_mcp_permission_policy_denies_server():
    """MCP tool denied if server not in allowed_servers."""
    registry = ToolRegistry()
    settings = Settings()
    policy = McpPermissionPolicy(
        config={
            "allowed_servers": ["filesystem"],  # github not allowed
        }
    )

    executor = ToolExecutor(
        registry=registry,
        settings=settings,
        mcp_permission_policy=policy,
    )

    # Register MCP tool from disallowed server
    tool_def = ToolDefinition(
        name="github.create_issue",
        description="Create a GitHub issue",
        parameters={"type": "object", "properties": {}},
    )
    handler = FakeMcpToolHandler(server_name="github", tool_name="create_issue")
    registry.register(tool_def, handler)

    # Authenticated user but server not allowed → forbidden
    context = ToolExecutionContext(
        caller=CallerContext(kind="user", user_id=uuid.uuid4())
    )
    call = ToolCall(name="github.create_issue", arguments={})

    result = await executor.execute(call, context)
    assert result.success is False
    assert result.error_code == "forbidden"
    assert result.error is not None and "github" in result.error
    assert (
        result.error is not None and "not in the allowed servers list" in result.error
    )


@pytest.mark.asyncio
async def test_tool_executor_guest_denial_inherited():
    """Guest caller denied by ToolAuthorizer even if MCP policy allows."""
    registry = ToolRegistry()
    settings = Settings()
    policy = McpPermissionPolicy(
        config={
            "allowed_servers": ["filesystem"],
            "allowed_tools": {"filesystem": ["*"]},
        }
    )

    executor = ToolExecutor(
        registry=registry,
        settings=settings,
        mcp_permission_policy=policy,
    )

    # Register MCP tool
    tool_def = ToolDefinition(
        name="filesystem.read_file",
        description="Read a file",
        parameters={"type": "object", "properties": {}},
    )
    handler = FakeMcpToolHandler(server_name="filesystem", tool_name="read_file")
    registry.register(tool_def, handler)

    # Guest caller → denied by ToolAuthorizer (before MCP policy check)
    context = ToolExecutionContext(
        caller=CallerContext(kind="guest", guest_id=uuid.uuid4())
    )
    call = ToolCall(name="filesystem.read_file", arguments={})

    result = await executor.execute(call, context)
    assert result.success is False
    assert result.error_code == "forbidden"
    assert result.error is not None and "authenticated user" in result.error


@pytest.mark.asyncio
async def test_tool_executor_local_tool_bypasses_mcp_policy():
    """Local (non-MCP) tools are not affected by McpPermissionPolicy."""
    registry = ToolRegistry()
    settings = Settings()
    policy = McpPermissionPolicy(
        config={
            "allowed_servers": ["filesystem"],  # Restrictive policy
        }
    )

    executor = ToolExecutor(
        registry=registry,
        settings=settings,
        mcp_permission_policy=policy,
    )

    # Register local tool (no MCP attributes)
    tool_def = ToolDefinition(
        name="web_search",
        description="Search the web",
        parameters={"type": "object", "properties": {}},
    )
    handler = FakeLocalToolHandler()
    registry.register(tool_def, handler)

    # Local tool should pass regardless of MCP policy
    context = ToolExecutionContext(
        caller=CallerContext(kind="user", user_id=uuid.uuid4())
    )
    call = ToolCall(name="web_search", arguments={})

    result = await executor.execute(call, context)
    assert result.success is True
    assert result.data == "local result"


@pytest.mark.asyncio
async def test_tool_executor_no_mcp_policy_allows_mcp_tool():
    """MCP tools allowed if no McpPermissionPolicy configured (backward compat)."""
    registry = ToolRegistry()
    settings = Settings()

    executor = ToolExecutor(
        registry=registry,
        settings=settings,
        mcp_permission_policy=None,  # No MCP policy
    )

    # Register MCP tool
    tool_def = ToolDefinition(
        name="filesystem.read_file",
        description="Read a file",
        parameters={"type": "object", "properties": {}},
    )
    handler = FakeMcpToolHandler(server_name="filesystem", tool_name="read_file")
    registry.register(tool_def, handler)

    # Without MCP policy, only ToolAuthorizer applies
    context = ToolExecutionContext(
        caller=CallerContext(kind="user", user_id=uuid.uuid4())
    )
    call = ToolCall(name="filesystem.read_file", arguments={})

    result = await executor.execute(call, context)
    assert result.success is True


@pytest.mark.asyncio
async def test_tool_executor_mcp_policy_wildcard_allows_all_tools():
    """McpPermissionPolicy with wildcard allows all tools from server."""
    registry = ToolRegistry()
    settings = Settings()
    policy = McpPermissionPolicy(
        config={
            "allowed_servers": ["filesystem"],
            "allowed_tools": {"filesystem": ["*"]},
        }
    )

    executor = ToolExecutor(
        registry=registry,
        settings=settings,
        mcp_permission_policy=policy,
    )

    # Register multiple MCP tools
    for tool_name in ["read_file", "write_file", "delete_file"]:
        tool_def = ToolDefinition(
            name=f"filesystem.{tool_name}",
            description=f"{tool_name} operation",
            parameters={"type": "object", "properties": {}},
        )
        handler = FakeMcpToolHandler(server_name="filesystem", tool_name=tool_name)
        registry.register(tool_def, handler)

    # All tools should pass with wildcard
    context = ToolExecutionContext(
        caller=CallerContext(kind="user", user_id=uuid.uuid4())
    )

    for tool_name in ["read_file", "write_file", "delete_file"]:
        call = ToolCall(name=f"filesystem.{tool_name}", arguments={})
        result = await executor.execute(call, context)
        assert result.success is True, f"Tool {tool_name} should succeed with wildcard"


@pytest.mark.asyncio
async def test_tool_executor_both_policies_must_pass():
    """Both ToolAuthorizer and McpPermissionPolicy must pass for execution."""
    registry = ToolRegistry()
    settings = Settings()
    policy = McpPermissionPolicy(
        config={
            "allowed_servers": ["filesystem"],
            "allowed_tools": {"filesystem": ["read_file"]},
        }
    )

    executor = ToolExecutor(
        registry=registry,
        settings=settings,
        mcp_permission_policy=policy,
    )

    tool_def = ToolDefinition(
        name="filesystem.read_file",
        description="Read a file",
        parameters={"type": "object", "properties": {}},
    )
    handler = FakeMcpToolHandler(server_name="filesystem", tool_name="read_file")
    registry.register(tool_def, handler)

    # Guest caller → ToolAuthorizer fails (MCP policy not even checked)
    guest_context = ToolExecutionContext(
        caller=CallerContext(kind="guest", guest_id=uuid.uuid4())
    )
    guest_call = ToolCall(name="filesystem.read_file", arguments={})
    guest_result = await executor.execute(guest_call, guest_context)
    assert guest_result.success is False
    assert guest_result.error is not None and "authenticated user" in guest_result.error

    # Authenticated user + allowed tool → both pass
    user_context = ToolExecutionContext(
        caller=CallerContext(kind="user", user_id=uuid.uuid4())
    )
    user_call = ToolCall(name="filesystem.read_file", arguments={})
    user_result = await executor.execute(user_call, user_context)
    assert user_result.success is True
