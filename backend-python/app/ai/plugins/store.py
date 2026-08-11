"""Read-only plugin inventory façade for the REST API."""

from __future__ import annotations

from app.ai.plugins.models import (
    PluginLoadFailureReason,
    PluginRecord,
)
from app.ai.plugins.registry import PluginRegistry
from app.schemas.plugins import (
    PluginDependencyResponse,
    PluginInventoryDetail,
    PluginInventoryItem,
    PluginInventoryListResponse,
    PluginLoadFailureResponse,
)


def _to_failure_response(
    reason: PluginLoadFailureReason | None,
) -> PluginLoadFailureResponse | None:
    if reason is None:
        return None
    include_api_fields = reason.code == "unsupported_api_version"
    return PluginLoadFailureResponse(
        code=reason.code,
        message=reason.message,
        expected_api_versions=(
            reason.expected_api_versions if include_api_fields else None
        ),
        manifest_api_version=(
            reason.manifest_api_version if include_api_fields else None
        ),
    )


def _to_inventory_item(record: PluginRecord) -> PluginInventoryItem:
    return PluginInventoryItem(
        plugin_id=record.plugin_id,
        name=record.name,
        version=record.version,
        api_version=record.api_version,
        status=record.status,
        contributions=list(record.contributions),
        load_duration_ms=record.load_duration_ms,
        author=record.author,
        homepage=record.homepage,
        repository=record.repository,
        documentation=record.documentation,
        license=record.license,
        failure=_to_failure_response(record.failure),
    )


class PluginsStore:
    """Maps in-memory ``PluginRegistry`` records to bounded inventory DTOs."""

    def __init__(self, registry: PluginRegistry) -> None:
        self._registry = registry

    def list_inventory(self) -> PluginInventoryListResponse:
        return PluginInventoryListResponse(
            plugins=[
                _to_inventory_item(record) for record in self._registry.list_records()
            ]
        )

    def get_detail(self, plugin_id: str) -> PluginInventoryDetail | None:
        record = self._registry.get(plugin_id)
        if record is None or record.plugin_id is None:
            return None
        item = _to_inventory_item(record)
        return PluginInventoryDetail(
            **item.model_dump(),
            dependencies=[
                PluginDependencyResponse(
                    plugin_id=dependency.plugin_id,
                    version=dependency.version,
                )
                for dependency in record.dependencies
            ],
        )
