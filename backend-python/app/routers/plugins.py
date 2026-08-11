"""Authenticated read-only Plugin inventory REST API (Epic 08 Phase 6)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.ai.deps import get_plugins_store
from app.ai.plugins.store import PluginsStore
from app.core.caller import CallerContext, require_authenticated_caller
from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.logging import bind_context
from app.schemas.plugins import PluginInventoryDetail, PluginInventoryListResponse

router = APIRouter()


def _require_plugins_enabled(settings: Settings) -> None:
    if not settings.plugins_enabled:
        raise AppError(
            code="feature_disabled",
            message="Plugins are not enabled on this server.",
            status_code=503,
        )


@router.get("/api/plugins", response_model=PluginInventoryListResponse)
async def list_plugins(
    caller: CallerContext = Depends(require_authenticated_caller),
    settings: Settings = Depends(get_settings),
    store: PluginsStore = Depends(get_plugins_store),
) -> PluginInventoryListResponse:
    _require_plugins_enabled(settings)
    bind_context(user_id=str(caller.user_id))
    return store.list_inventory()


@router.get("/api/plugins/{plugin_id}", response_model=PluginInventoryDetail)
async def get_plugin_detail(
    plugin_id: str,
    caller: CallerContext = Depends(require_authenticated_caller),
    settings: Settings = Depends(get_settings),
    store: PluginsStore = Depends(get_plugins_store),
) -> PluginInventoryDetail:
    _require_plugins_enabled(settings)
    bind_context(user_id=str(caller.user_id))
    detail = store.get_detail(plugin_id)
    if detail is None:
        raise AppError(
            code="plugin_not_found",
            message=f"Plugin '{plugin_id}' was not found.",
            status_code=404,
        )
    return detail
