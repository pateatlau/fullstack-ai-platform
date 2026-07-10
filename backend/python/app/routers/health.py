from fastapi import APIRouter, Depends

from app.core.config import APP_VERSION, Settings, get_settings

router = APIRouter()


@router.get("/api/health")
async def health(settings: Settings = Depends(get_settings)) -> dict[str, str]:
    return {
        "status": "ok",
        "provider": settings.llm_provider,
        "version": APP_VERSION,
    }
