from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import APP_VERSION, Settings, get_settings
from app.core.errors import DATABASE_ERROR_MESSAGE, error_response
from app.db.engine import get_engine
from app.providers.capabilities import capabilities_by_provider

router = APIRouter()


@router.get("/api/health")
async def health(settings: Settings = Depends(get_settings)) -> dict[str, object]:
    return {
        "status": "ok",
        "provider": settings.llm_provider,
        "version": APP_VERSION,
        "chat_streaming_enabled": settings.chat_streaming_enabled,
        "tools_enabled": settings.tools_enabled,
        "capabilities": {
            "by_provider": capabilities_by_provider(),
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
