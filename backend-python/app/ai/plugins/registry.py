"""In-memory plugin inventory registry."""

from __future__ import annotations

from app.ai.plugins.manifest import PluginManifest, manifest_identity_fields
from app.ai.plugins.models import (
    PluginContributionKind,
    PluginDependency,
    PluginLoadFailureReason,
    PluginRecord,
    PluginStatus,
)


class PluginRegistry:
    """Process-wide plugin load state (immutable after startup load)."""

    def __init__(self) -> None:
        self._records: list[PluginRecord] = []

    def list_records(self) -> list[PluginRecord]:
        return list(self._records)

    def get(self, plugin_id: str) -> PluginRecord | None:
        for record in self._records:
            if record.plugin_id == plugin_id:
                return record
        return None

    @property
    def loaded_count(self) -> int:
        return sum(
            1 for record in self._records if record.status == PluginStatus.LOADED
        )

    @property
    def failed_count(self) -> int:
        return sum(
            1 for record in self._records if record.status == PluginStatus.FAILED
        )

    def mark_loaded(
        self,
        manifest: PluginManifest,
        *,
        contributions: list[PluginContributionKind],
        load_duration_ms: float,
    ) -> PluginRecord:
        record = PluginRecord(
            **manifest_identity_fields(manifest),
            status=PluginStatus.LOADED,
            contributions=contributions,
            load_duration_ms=load_duration_ms,
        )
        self._records.append(record)
        return record

    def mark_failed(
        self,
        *,
        failure: PluginLoadFailureReason,
        load_duration_ms: float = 0.0,
        manifest: PluginManifest | None = None,
        plugin_id: str | None = None,
        name: str | None = None,
        version: str | None = None,
        api_version: str | None = None,
        author: str | None = None,
        homepage: str | None = None,
        repository: str | None = None,
        documentation: str | None = None,
        license: str | None = None,
        dependencies: list[PluginDependency] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> PluginRecord:
        if manifest is not None:
            identity = manifest_identity_fields(manifest)
            record = PluginRecord(
                **identity,
                status=PluginStatus.FAILED,
                load_duration_ms=load_duration_ms,
                failure=failure,
            )
        else:
            record = PluginRecord(
                plugin_id=plugin_id,
                name=name,
                version=version,
                api_version=api_version,
                status=PluginStatus.FAILED,
                load_duration_ms=load_duration_ms,
                author=author,
                homepage=homepage,
                repository=repository,
                documentation=documentation,
                license=license,
                dependencies=dependencies or [],
                metadata=metadata or {},
                failure=failure,
            )
        self._records.append(record)
        return record

    def reset_for_tests(self) -> None:
        self._records.clear()
