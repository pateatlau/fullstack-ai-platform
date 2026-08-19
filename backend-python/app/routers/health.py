from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.ai.deps import get_approvals_store, get_job_queue, get_plugin_registry
from app.ai.hitl.store import ApprovalsStore
from app.ai.jobs.queue import JobQueue
from app.ai.plugins.registry import PluginRegistry
from app.core.config import APP_VERSION, Settings, get_settings
from app.core.errors import DATABASE_ERROR_MESSAGE, error_response
from app.db.engine import get_engine
from app.providers.capabilities import capabilities_by_provider

router = APIRouter()


async def _hitl_pending_approvals_count(
    *,
    hitl_enabled: bool,
    approvals_store: ApprovalsStore,
) -> int:
    """Best-effort pending count for liveness; never fail ``/api/health`` on DB errors."""
    if not hitl_enabled:
        return 0
    try:
        return await approvals_store.count_pending()
    except Exception:
        return 0


async def _background_jobs_counts(
    *,
    background_jobs_enabled: bool,
    job_queue: JobQueue,
) -> tuple[int, int]:
    """Best-effort queue depth counts; never fail ``/api/health`` on DB errors."""
    if not background_jobs_enabled:
        return 0, 0
    try:
        pending = await job_queue.count_pending()
        dead_letter = await job_queue.count_dead_letter()
        return pending, dead_letter
    except Exception:
        return 0, 0


@router.get("/api/health")
async def health(
    settings: Settings = Depends(get_settings),
    plugin_registry: PluginRegistry = Depends(get_plugin_registry),
    approvals_store: ApprovalsStore = Depends(get_approvals_store),
    job_queue: JobQueue = Depends(get_job_queue),
) -> dict[str, object]:
    plugins_enabled = settings.plugins_enabled
    hitl_enabled = settings.hitl_enabled
    background_jobs_enabled = settings.background_jobs_enabled
    hitl_pending_approvals_count = await _hitl_pending_approvals_count(
        hitl_enabled=hitl_enabled,
        approvals_store=approvals_store,
    )
    (
        background_jobs_pending_count,
        background_jobs_dead_letter_count,
    ) = await _background_jobs_counts(
        background_jobs_enabled=background_jobs_enabled,
        job_queue=job_queue,
    )
    return {
        "status": "ok",
        "provider": settings.llm_provider,
        "version": APP_VERSION,
        "chat_streaming_enabled": settings.chat_streaming_enabled,
        "tools_enabled": settings.tools_enabled,
        "rag_enabled": settings.rag_enabled,
        "voice_enabled": settings.voice_enabled,
        "memory_enabled": settings.memory_enabled,
        "workflow_engine_enabled": settings.workflow_engine_enabled,
        "observability_enabled": settings.observability_enabled,
        "security_governance_enabled": settings.security_governance_enabled,
        "rbac_enforcement_enabled": settings.security_rbac_enforcement_enabled,
        "guardrails_enabled": settings.security_guardrails_enabled,
        "hitl_enabled": hitl_enabled,
        "hitl_pending_approvals_count": hitl_pending_approvals_count,
        "background_jobs_enabled": background_jobs_enabled,
        "background_jobs_pending_count": background_jobs_pending_count,
        "background_jobs_dead_letter_count": background_jobs_dead_letter_count,
        "plugins_enabled": plugins_enabled,
        "plugins_loaded_count": (
            plugin_registry.loaded_count if plugins_enabled else 0
        ),
        "plugins_failed_count": (
            plugin_registry.failed_count if plugins_enabled else 0
        ),
        "capabilities": {
            "by_provider": capabilities_by_provider(settings),
        },
    }


@router.get("/api/health/ready")
async def readiness() -> JSONResponse:
    """Readiness probe (plan Section 6.1): a lightweight DB ``SELECT 1``.

    Liveness (``/api/health``) stays DB-independent so a DB blip doesn't kill the
    container; readiness gates traffic/deploy verification.
    """
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        return error_response(503, "database_error", DATABASE_ERROR_MESSAGE)
    return JSONResponse(status_code=200, content={"status": "ok", "db": "ok"})
