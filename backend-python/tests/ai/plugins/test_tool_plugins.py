"""Tool plugin integration tests (Epic 08 Phase 2)."""

from __future__ import annotations

import uuid

import pytest

from app.ai.plugins import PluginContributionKind, PluginStatus
from app.ai.plugins.registrar import PluginRegistrar
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
from tests.ai.plugins.conftest import load_plugins, plugin_settings

TOOL_NAME = "com.test.tool.echo"


@pytest.fixture
def tool_registry() -> ToolRegistry:
    return ToolRegistry()


@pytest.fixture
def executor(tool_registry: ToolRegistry) -> ToolExecutor:
    return ToolExecutor(
        registry=tool_registry,
        settings=Settings(request_timeout_seconds=5),
    )


@pytest.fixture
def user_context() -> ToolExecutionContext:
    return ToolExecutionContext(
        caller=CallerContext.for_user(uuid.uuid4()),
        request_id="req-plugin-tool",
    )


class TestRegistrarToolPrefix:
    def test_unprefixed_name_rejected(self, tmp_path) -> None:
        registrar = PluginRegistrar(
            plugin_id="com.test.prefix",
            plugin_dir=tmp_path,
            tool_registry=ToolRegistry(),
        )

        class _Handler:
            async def execute(
                self,
                args: dict[str, object],
                context: ToolExecutionContext,
            ) -> ToolResult:
                del args, context
                return ToolResult(success=True)

        with pytest.raises(Exception, match="must start with"):
            registrar.register_tool(
                ToolDefinition(
                    name="not-prefixed",
                    description="bad",
                    parameters={},
                ),
                _Handler(),
            )


class TestToolPluginLoad:
    def test_tool_registered_in_registry(self, tool_registry: ToolRegistry) -> None:
        _, registry, tools = load_plugins(
            plugin_settings(allowlist=["com.test.tool"]),
            tool_registry=tool_registry,
        )
        record = registry.get("com.test.tool")
        assert record is not None
        assert record.status == PluginStatus.LOADED
        assert PluginContributionKind.TOOL in record.contributions
        assert tools.get(TOOL_NAME) is not None
        assert tools.get_handler(TOOL_NAME) is not None

    def test_unprefixed_tool_fails_plugin(self, tool_registry: ToolRegistry) -> None:
        _, registry, tools = load_plugins(
            plugin_settings(allowlist=["com.test.bad-prefix"]),
            tool_registry=tool_registry,
        )
        record = registry.get("com.test.bad-prefix")
        assert record is not None
        assert record.status == PluginStatus.FAILED
        assert record.failure is not None
        assert record.failure.code == "registration_error"
        assert "must start with" in record.failure.message
        assert tools.get("unprefixed.echo") is None

    def test_duplicate_tool_name_fails_second_plugin(
        self,
        tool_registry: ToolRegistry,
    ) -> None:
        _, registry, tools = load_plugins(
            plugin_settings(
                allowlist=["com", "com.test"],
            ),
            tool_registry=tool_registry,
        )
        first = registry.get("com")
        second = registry.get("com.test")
        assert first is not None
        assert second is not None
        assert first.status == PluginStatus.LOADED
        assert second.status == PluginStatus.FAILED
        assert second.failure is not None
        assert second.failure.code == "registration_error"
        assert "already registered" in second.failure.message
        assert tools.get("com.test.shared") is not None

    def test_flag_off_tool_not_registered(self, tool_registry: ToolRegistry) -> None:
        load_plugins(
            plugin_settings(
                enabled=False,
                allowlist=["com.test.tool"],
            ),
            tool_registry=tool_registry,
        )
        assert tool_registry.get(TOOL_NAME) is None


class TestToolPluginExecution:
    @pytest.mark.anyio
    async def test_tool_callable_via_executor(
        self,
        tool_registry: ToolRegistry,
        executor: ToolExecutor,
        user_context: ToolExecutionContext,
    ) -> None:
        load_plugins(
            plugin_settings(allowlist=["com.test.tool"]),
            tool_registry=tool_registry,
        )
        result = await executor.execute(
            ToolCall(name=TOOL_NAME, arguments={"message": "hello plugin"}),
            user_context,
        )
        assert result.success is True
        assert result.data == {"message": "hello plugin"}

    @pytest.mark.anyio
    async def test_staging_fixture_tool_via_executor(
        self,
        tool_registry: ToolRegistry,
        executor: ToolExecutor,
        user_context: ToolExecutionContext,
    ) -> None:
        load_plugins(
            plugin_settings(allowlist=["com.test.staging"]),
            tool_registry=tool_registry,
        )
        result = await executor.execute(
            ToolCall(
                name="com.test.staging.echo",
                arguments={},
            ),
            user_context,
        )
        assert result.success is True
