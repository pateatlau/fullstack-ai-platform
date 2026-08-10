"""Plugin SDK foundation tests (Epic 08 Phase 1)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from app.ai.plugins import (
    PLUGIN_API_VERSION,
    SUPPORTED_PLUGIN_API_VERSIONS,
    PluginContributionKind,
    PluginLoader,
    PluginManifestError,
    PluginRegistrar,
    PluginRegistrationError,
    PluginRegistry,
    PluginStatus,
)
from app.ai.plugins.manifest import load_manifest_from_yaml
from app.ai.tools.schemas import ToolDefinition, ToolExecutionContext, ToolResult
from tests.ai.plugins.conftest import FIXTURES_ROOT, load_plugins, plugin_settings


class TestPublicApi:
    def test_constants(self) -> None:
        assert PLUGIN_API_VERSION == "1"
        assert SUPPORTED_PLUGIN_API_VERSIONS == frozenset({"1"})


class TestManifest:
    def test_valid_minimal_manifest(self) -> None:
        manifest = load_manifest_from_yaml(
            FIXTURES_ROOT / "minimal-plugin" / "plugin.yaml"
        )
        assert manifest.plugin_id == "com.test.minimal"
        assert manifest.api_version == "1"
        assert manifest.version == "1.0.0"

    def test_invalid_api_version_in_yaml_still_parses(self) -> None:
        manifest = load_manifest_from_yaml(
            FIXTURES_ROOT / "unsupported-api" / "plugin.yaml"
        )
        assert manifest.api_version == "2"

    def test_rejects_unknown_top_level_key(self, tmp_path: Path) -> None:
        path = tmp_path / "plugin.yaml"
        path.write_text(
            "plugin_id: com.test.unknown\n"
            "name: Unknown\n"
            "version: 1.0.0\n"
            "api_version: '1'\n"
            "entrypoint: mod:register\n"
            "unexpected: true\n",
            encoding="utf-8",
        )
        with pytest.raises(PluginManifestError, match="unexpected"):
            load_manifest_from_yaml(path)

    def test_rich_metadata_round_trip(self) -> None:
        manifest = load_manifest_from_yaml(
            FIXTURES_ROOT / "rich-metadata" / "plugin.yaml"
        )
        assert manifest.author == "Example Corp"
        assert manifest.homepage == "https://example.com"
        assert len(manifest.dependencies) == 1
        assert manifest.dependencies[0].plugin_id == "com.example.memory"
        assert manifest.dependencies[0].version == ">=1.0.0"
        assert manifest.metadata == {"team": "platform", "tier": "reference"}


class TestRegistrarStaging:
    def test_commit_promotes_staged_contributions(self, tmp_path: Path) -> None:
        registrar = PluginRegistrar(
            plugin_id="com.test.staging",
            plugin_dir=tmp_path,
        )

        class _Handler:
            async def execute(
                self,
                args: dict[str, object],
                context: ToolExecutionContext,
            ) -> ToolResult:
                del args, context
                return ToolResult(success=True)

        registrar.register_tool(
            ToolDefinition(
                name="com.test.staging.echo", description="d", parameters={}
            ),
            _Handler(),
        )
        registrar.register_prompt_template(
            name="greeting",
            version="1",
            source="Hi",
        )
        registrar.commit()

        assert len(registrar.committed.tools) == 1
        assert len(registrar.committed.prompts) == 1
        assert registrar.contribution_kinds() == [
            PluginContributionKind.TOOL,
            PluginContributionKind.PROMPT,
        ]

    def test_rollback_clears_staging(self, tmp_path: Path) -> None:
        registrar = PluginRegistrar(
            plugin_id="com.test.rollback",
            plugin_dir=tmp_path,
        )
        registrar.register_prompt_template(name="x", version="1", source="y")
        registrar.rollback()
        registrar.commit()
        assert registrar.committed.prompts == []

    def test_closed_registrar_rejects_late_staging(self, tmp_path: Path) -> None:
        registrar = PluginRegistrar(
            plugin_id="com.test.closed",
            plugin_dir=tmp_path,
        )
        registrar.close()
        with pytest.raises(PluginRegistrationError, match="closed"):
            registrar.register_prompt_template(name="x", version="1", source="y")


class TestPluginLoader:
    def test_flag_off_returns_empty_report(self) -> None:
        report, registry, _tools, _prompts = load_plugins(
            plugin_settings(enabled=False)
        )
        assert report.loaded_count == 0
        assert report.failed_count == 0
        assert registry.list_records() == []

    def test_loads_minimal_plugin(self) -> None:
        plugin_dir = str((FIXTURES_ROOT / "minimal-plugin").resolve())
        sys_path_before = list(sys.path)
        report, registry, _tools, _prompts = load_plugins(
            plugin_settings(
                allowlist=["com.test.minimal"],
            )
        )
        assert plugin_dir not in sys.path
        assert sys.path == sys_path_before
        assert report.loaded_count == 1
        record = registry.get("com.test.minimal")
        assert record is not None
        assert record.status == PluginStatus.LOADED
        assert record.load_duration_ms >= 0

    def test_unsupported_api_version_failure_reason(self) -> None:
        _, registry, _tools, _prompts = load_plugins(
            plugin_settings(
                allowlist=["com.test.unsupported"],
            )
        )
        record = registry.get("com.test.unsupported")
        assert record is not None
        assert record.status == PluginStatus.FAILED
        assert record.failure is not None
        assert record.failure.code == "unsupported_api_version"
        assert record.failure.expected_api_versions == ["1"]
        assert record.failure.manifest_api_version == "2"

    def test_duplicate_plugin_id_fails_second(self) -> None:
        _, registry, _tools, _prompts = load_plugins(
            plugin_settings(
                directories=[str(FIXTURES_ROOT)],
                allowlist=["com.test.duplicate"],
            )
        )
        duplicate_records = [
            record
            for record in registry.list_records()
            if record.plugin_id == "com.test.duplicate"
        ]
        assert len(duplicate_records) == 2
        statuses = {record.status for record in duplicate_records}
        assert PluginStatus.LOADED in statuses
        assert PluginStatus.FAILED in statuses

    def test_entrypoint_import_error(self) -> None:
        _, registry, _tools, _prompts = load_plugins(
            plugin_settings(
                allowlist=["com.test.bad-entrypoint"],
            )
        )
        record = registry.get("com.test.bad-entrypoint")
        assert record is not None
        assert record.failure is not None
        assert record.failure.code == "entrypoint_import_error"

    def test_registration_timeout(self) -> None:
        report, registry, _tools, _prompts = load_plugins(
            plugin_settings(
                allowlist=["com.test.slow"],
                timeout_seconds=1,
            )
        )
        record = registry.get("com.test.slow")
        assert record is not None
        assert record.failure is not None
        assert record.failure.code == "timeout"
        assert "within 1s wait limit" in record.failure.message
        # Slow plugin sleeps 2s; load must not block until it finishes.
        assert report.total_load_duration_ms < 1800
        assert record.load_duration_ms < 1800

    def test_allowlist_excludes_plugin(self) -> None:
        _, registry, _tools, _prompts = load_plugins(
            plugin_settings(
                allowlist=["com.other.plugin"],
            )
        )
        record = registry.get("com.test.minimal")
        assert record is not None
        assert record.failure is not None
        assert record.failure.code == "allowlist_excluded"

    def test_manifest_not_found_record(self, tmp_path: Path) -> None:
        (tmp_path / "empty-plugin").mkdir()
        _, registry, _tools, _prompts = load_plugins(
            plugin_settings(directories=[str(tmp_path)]),
        )
        assert len(registry.list_records()) == 1
        record = registry.list_records()[0]
        assert record.plugin_id is None
        assert record.failure is not None
        assert record.failure.code == "manifest_not_found"

    def test_rich_metadata_on_loaded_record(self) -> None:
        _, registry, _tools, _prompts = load_plugins(
            plugin_settings(
                allowlist=["com.test.rich"],
            )
        )
        record = registry.get("com.test.rich")
        assert record is not None
        assert record.status == PluginStatus.LOADED
        assert record.author == "Example Corp"
        assert record.dependencies[0].plugin_id == "com.example.memory"
        assert record.metadata["team"] == "platform"

    def test_staging_plugin_contributions(self) -> None:
        _, registry, _tools, _prompts = load_plugins(
            plugin_settings(
                allowlist=["com.test.staging"],
            )
        )
        record = registry.get("com.test.staging")
        assert record is not None
        assert record.status == PluginStatus.LOADED
        assert PluginContributionKind.TOOL in record.contributions
        assert PluginContributionKind.PROMPT in record.contributions

    def test_load_all_never_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        registry = PluginRegistry()
        settings = plugin_settings(allowlist=["com.test.minimal"])

        def _boom(self: PluginLoader) -> list[object]:
            raise RuntimeError("discovery exploded")

        monkeypatch.setattr(PluginLoader, "discover", _boom)
        loader = PluginLoader(settings, registry)
        report = loader.load_all()
        assert report.records == []

    def test_invalid_manifest_partial_identity(self, tmp_path: Path) -> None:
        plugin_dir = tmp_path / "invalid-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.yaml").write_text(
            "plugin_id: INVALID\n"
            "name: Bad ID\n"
            "version: not-semver\n"
            "api_version: '1'\n"
            "entrypoint: mod:register\n",
            encoding="utf-8",
        )
        _, registry, _tools, _prompts = load_plugins(
            plugin_settings(directories=[str(tmp_path)]),
        )
        record = registry.list_records()[0]
        assert record.plugin_id is None
        assert record.name == "Bad ID"
        assert record.failure is not None
        assert record.failure.code == "invalid_manifest"
