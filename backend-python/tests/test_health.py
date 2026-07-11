import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import APP_VERSION, get_settings
from app.main import app


@pytest.mark.anyio
async def test_health_returns_expected_shape() -> None:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "provider": get_settings().llm_provider,
        "version": APP_VERSION,
    }
