"""MCP server plugin tests (Epic 08 Phase 5)."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from app.ai.mcp.registry import McpServerRegistry
from app.ai.plugins.models import PluginContributionKind, PluginStatus
from app.ai.tools.registration import _merge_mcp_server_configs, register_mcp_tools
from app.ai.tools.registry import ToolRegistry
from app.core.config import Settings
from tests.ai.plugins.conftest import load_plugins, plugin_settings

pytestmark = pytest.mark.anyio


class TestPluginMcpAggregation:
    def test_manifest_mcp_servers_loaded(self) -> None:
        _, registry, _, _ = load_plugins(
            plugin_settings(allowlist=["com.test.mcp-manifest"])
        )
        record = registry.get("com.test.mcp-manifest")
        assert record is not None
        assert record.status == PluginStatus.LOADED
        assert PluginContributionKind.MCP_SERVER in record.contributions
        servers = registry.list_mcp_servers()
        assert len(servers) == 1
        assert servers[0]["name"] == "manifest-mcp-server"

    def test_programmatic_mcp_server_loaded(self) -> None:
        _, registry, _, _ = load_plugins(
            plugin_settings(allowlist=["com.test.mcp-programmatic"])
        )
        record = registry.get("com.test.mcp-programmatic")
        assert record is not None
        assert record.status == PluginStatus.LOADED
        assert PluginContributionKind.MCP_SERVER in record.contributions
        servers = registry.list_mcp_servers()
        assert any(server["name"] == "programmatic-mcp-server" for server in servers)

    def test_invalid_mcp_config_fails_plugin_only(self) -> None:
        _, registry, _, _ = load_plugins(
            plugin_settings(allowlist=["com.test.bad-mcp"])
        )
        record = registry.get("com.test.bad-mcp")
        assert record is not None
        assert record.status == PluginStatus.FAILED
        assert record.failure is not None
        assert record.failure.code == "registration_error"
        assert "Invalid MCP server config" in record.failure.message
        assert registry.list_mcp_servers() == []

    def test_api_version_mismatch_structured_failure(self) -> None:
        _, registry, _, _ = load_plugins(
            plugin_settings(allowlist=["com.test.unsupported"])
        )
        record = registry.get("com.test.unsupported")
        assert record is not None
        assert record.failure is not None
        assert record.failure.code == "unsupported_api_version"
        assert record.failure.expected_api_versions == ["1"]
        assert record.failure.manifest_api_version == "2"


class TestMcpServerMerge:
    def test_env_wins_on_name_conflict(self, caplog: pytest.LogCaptureFixture) -> None:
        env_servers = [
            {
                "name": "shared-server",
                "command": "env-command",
                "args": [],
                "transport": "stdio",
            }
        ]
        plugin_servers = [
            {
                "name": "shared-server",
                "command": "plugin-command",
                "args": [],
                "transport": "stdio",
            },
            {
                "name": "plugin-only-server",
                "command": "plugin-only",
                "args": [],
                "transport": "stdio",
            },
        ]

        with caplog.at_level(logging.WARNING):
            merged = _merge_mcp_server_configs(
                env_servers=env_servers,
                plugin_servers=plugin_servers,
            )

        assert len(merged) == 2
        assert merged[0]["command"] == "env-command"
        assert merged[1]["name"] == "plugin-only-server"
        assert any(
            "env config wins on name conflict" in record.message
            for record in caplog.records
        )

    async def test_plugin_mcp_skipped_when_mcp_disabled(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        settings = Settings(
            openai_api_key="test-key",
            mcp_enabled=False,
            mcp_servers=[],
        )
        registry = ToolRegistry()
        mcp_registry = McpServerRegistry(connection_timeout=10.0, tool_timeout=30.0)

        with caplog.at_level(logging.DEBUG):
            await register_mcp_tools(
                registry=registry,
                mcp_registry=mcp_registry,
                settings=settings,
                extra_servers=[
                    {
                        "name": "plugin-server",
                        "command": "plugin-command",
                        "args": [],
                        "transport": "stdio",
                    }
                ],
            )

        assert registry.list_tools() == []
        assert any(
            "skipping plugin-declared MCP servers" in record.message
            for record in caplog.records
        )

    async def test_extra_servers_registered_when_no_env_conflict(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        settings = Settings(
            openai_api_key="test-key",
            mcp_enabled=True,
            mcp_servers=[],
            mcp_permission_policy={},
        )
        registry = ToolRegistry()
        mcp_registry = McpServerRegistry(connection_timeout=10.0, tool_timeout=30.0)
        registered_names: list[str] = []

        async def _fake_register(
            self: McpServerRegistry, name: str, config: object
        ) -> None:
            del config
            registered_names.append(name)

        monkeypatch.setattr(McpServerRegistry, "register", _fake_register)
        monkeypatch.setattr(
            "app.ai.mcp.discovery.McpToolDiscovery.discover",
            AsyncMock(return_value=[]),
        )

        await register_mcp_tools(
            registry=registry,
            mcp_registry=mcp_registry,
            settings=settings,
            extra_servers=[
                {
                    "name": "plugin-only-server",
                    "command": "plugin-command",
                    "args": [],
                    "transport": "stdio",
                }
            ],
        )

        assert registered_names == ["plugin-only-server"]
