"""Observability REST API integration tests (Epic 07 Phase 6)."""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Iterator
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI
from sqlalchemy import text

from app.ai.observability.cost.calculator import CostCalculator, CostRegistry
from app.ai.observability.cost.pricing import ModelPricingTable
from app.ai.observability.metrics.instruments import MetricInstruments
from app.ai.observability.metrics.meter import MeterRegistry
from app.ai.observability.tracing.provider import TracerRegistry
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.security import create_access_token, generate_guest_token, hash_token
from app.db.chat import SqlChatStore
from app.db.identity import SqlGuestStore, SqlUserStore
from app.db.models import UsageEvent
from app.db.session import get_db_session
from app.db.usage import SqlUsageStore
from app.routers import health as health_router
from app.routers import observability as observability_router

_NOW = datetime.datetime.now(datetime.UTC)
_TODAY = _NOW.date()


def _observability_settings() -> Settings:
    return Settings(openai_api_key="test-key", observability_enabled=True)


def _auth_headers(user_id: uuid.UUID) -> dict[str, str]:
    token = create_access_token(user_id=user_id, settings=get_settings())
    return {"Authorization": f"Bearer {token}"}


def _build_test_app(*, include_health: bool = False) -> FastAPI:
    test_app = FastAPI()
    test_app.include_router(observability_router.router)
    if include_health:
        test_app.include_router(health_router.router)
    register_exception_handlers(test_app)
    return test_app


def _bind_db_session(test_app: FastAPI, session) -> None:
    async def _override_db_session():
        yield session

    test_app.dependency_overrides[get_db_session] = _override_db_session


def _clear_router_overrides(test_app: FastAPI) -> None:
    test_app.dependency_overrides.pop(get_db_session, None)


async def _make_user(session) -> uuid.UUID:
    user = await SqlUserStore(session).create(
        sub=f"obs-api-{uuid.uuid4()}",
        email=None,
        name=None,
        picture=None,
    )
    return user.id


async def _make_guest(session) -> tuple[uuid.UUID, str]:
    token = generate_guest_token()
    guest = await SqlGuestStore(session).create(
        token_hash=hash_token(token),
        created_ip_hash=None,
    )
    return guest.id, token


async def _usage_cost_columns_available(session) -> bool:
    result = await session.execute(
        text(
            """
            SELECT COUNT(*)
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'usage_events'
              AND column_name IN ('cost_usd', 'pricing_version')
            """
        )
    )
    return int(result.scalar_one()) == 2


async def _skip_without_usage_cost_columns(session) -> None:
    if not await _usage_cost_columns_available(session):
        pytest.skip(
            "usage_events cost columns not available — run alembic upgrade head"
        )


def _init_observability_registries() -> None:
    TracerRegistry.reset_for_tests()
    MeterRegistry.reset_for_tests()
    CostRegistry.reset_for_tests()
    MetricInstruments._instance = None

    settings = _observability_settings()
    TracerRegistry.initialize(settings)
    MeterRegistry.initialize(settings)
    table = ModelPricingTable.load(settings)
    CostRegistry._initialized = True
    CostRegistry._calculator = CostCalculator(table)
    MetricInstruments.initialize()


def _reset_observability_registries() -> None:
    TracerRegistry.reset_for_tests()
    MeterRegistry.reset_for_tests()
    CostRegistry.reset_for_tests()
    MetricInstruments._instance = None


async def _seed_usage_event(
    session,
    *,
    user_id: uuid.UUID | None,
    guest_id: uuid.UUID | None,
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    created_at: datetime.datetime,
    cost_usd: Decimal | None = Decimal("0.010000"),
) -> None:
    chat_store = SqlChatStore(session)
    if user_id is not None:
        chat = await chat_store.create_session(user_id=user_id)
    else:
        chat = await chat_store.create_session(guest_id=guest_id)
    event = UsageEvent(
        session_id=chat.id,
        user_id=user_id,
        guest_id=guest_id,
        provider=provider,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        token_source="provider_reported",
        cost_usd=cost_usd,
        pricing_version="2026-08" if cost_usd is not None else None,
        created_at=created_at,
    )
    session.add(event)
    await session.flush()


@pytest.fixture
def observability_api_app(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[FastAPI]:
    monkeypatch.setenv("OBSERVABILITY_ENABLED", "true")
    get_settings.cache_clear()
    _init_observability_registries()
    test_app = _build_test_app()
    _bind_db_session(test_app, db_session)
    try:
        yield test_app
    finally:
        _clear_router_overrides(test_app)
        _reset_observability_registries()
        get_settings.cache_clear()


@pytest.mark.anyio
async def test_usage_summary_happy_path_group_by_day(
    db_session,
    observability_api_app: FastAPI,
) -> None:
    await _skip_without_usage_cost_columns(db_session)
    user_id = await _make_user(db_session)
    await _seed_usage_event(
        db_session,
        user_id=user_id,
        guest_id=None,
        provider="openai",
        model="gpt-4o-mini",
        prompt_tokens=100,
        completion_tokens=50,
        created_at=_NOW,
    )

    async with AsyncClient(
        transport=ASGITransport(app=observability_api_app),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/observability/usage",
            headers=_auth_headers(user_id),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["group_by"] == "day"
    assert len(body["rows"]) == 1
    row = body["rows"][0]
    assert row["request_count"] == 1
    assert row["prompt_tokens"] == 100
    assert row["completion_tokens"] == 50
    assert row["total_tokens"] == 150
    assert row["cost_usd"] == pytest.approx(0.01)
    assert "session_id" not in body
    assert "trace_id" not in body


@pytest.mark.anyio
async def test_usage_summary_group_by_day_uses_utc_not_session_timezone(
    db_session,
    observability_api_app: FastAPI,
) -> None:
    """Day buckets must follow UTC to match UTC range bounds, not DB session TZ."""
    await _skip_without_usage_cost_columns(db_session)
    await db_session.execute(text("SET TIME ZONE 'America/New_York'"))

    user_id = await _make_user(db_session)
    # 2026-08-08 02:00 UTC is still 2026-08-07 in US/Eastern.
    created_at = datetime.datetime(2026, 8, 8, 2, 0, tzinfo=datetime.UTC)
    await _seed_usage_event(
        db_session,
        user_id=user_id,
        guest_id=None,
        provider="openai",
        model="gpt-4o-mini",
        prompt_tokens=10,
        completion_tokens=5,
        created_at=created_at,
    )

    async with AsyncClient(
        transport=ASGITransport(app=observability_api_app),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/observability/usage?group_by=day&since=2026-08-08&until=2026-08-08",
            headers=_auth_headers(user_id),
        )

    assert response.status_code == 200
    rows = response.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["day"] == "2026-08-08"


@pytest.mark.anyio
@pytest.mark.parametrize("group_by", ["day", "provider", "model"])
async def test_usage_summary_group_by_modes(
    db_session,
    observability_api_app: FastAPI,
    group_by: str,
) -> None:
    await _skip_without_usage_cost_columns(db_session)
    user_id = await _make_user(db_session)
    await _seed_usage_event(
        db_session,
        user_id=user_id,
        guest_id=None,
        provider="openai",
        model="gpt-4o-mini",
        prompt_tokens=10,
        completion_tokens=5,
        created_at=_NOW,
    )
    await _seed_usage_event(
        db_session,
        user_id=user_id,
        guest_id=None,
        provider="anthropic",
        model="claude-haiku-4-5-20251001",
        prompt_tokens=20,
        completion_tokens=10,
        created_at=_NOW,
    )

    async with AsyncClient(
        transport=ASGITransport(app=observability_api_app),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            f"/api/observability/usage?group_by={group_by}",
            headers=_auth_headers(user_id),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["group_by"] == group_by
    if group_by == "day":
        assert len(body["rows"]) == 1
        assert body["rows"][0]["day"] is not None
    elif group_by == "provider":
        assert len(body["rows"]) == 2
        providers = {row["provider"] for row in body["rows"]}
        assert providers == {"openai", "anthropic"}
    else:
        assert len(body["rows"]) == 2
        assert all(row["provider"] and row["model"] for row in body["rows"])


@pytest.mark.anyio
async def test_usage_summary_date_range_filter(
    db_session,
    observability_api_app: FastAPI,
) -> None:
    await _skip_without_usage_cost_columns(db_session)
    user_id = await _make_user(db_session)
    in_range = _NOW - datetime.timedelta(days=5)
    out_of_range = _NOW - datetime.timedelta(days=40)
    await _seed_usage_event(
        db_session,
        user_id=user_id,
        guest_id=None,
        provider="openai",
        model="gpt-4o-mini",
        prompt_tokens=10,
        completion_tokens=5,
        created_at=in_range,
    )
    await _seed_usage_event(
        db_session,
        user_id=user_id,
        guest_id=None,
        provider="openai",
        model="gpt-4o-mini",
        prompt_tokens=99,
        completion_tokens=99,
        created_at=out_of_range,
    )

    since = (_NOW - datetime.timedelta(days=10)).date().isoformat()
    until = _TODAY.isoformat()

    async with AsyncClient(
        transport=ASGITransport(app=observability_api_app),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            f"/api/observability/usage?since={since}&until={until}",
            headers=_auth_headers(user_id),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["since"] == since
    assert body["until"] == until
    assert len(body["rows"]) == 1
    assert body["rows"][0]["request_count"] == 1


@pytest.mark.anyio
async def test_usage_summary_owner_isolation(
    db_session,
    observability_api_app: FastAPI,
) -> None:
    await _skip_without_usage_cost_columns(db_session)
    owner_id = await _make_user(db_session)
    other_id = await _make_user(db_session)
    await _seed_usage_event(
        db_session,
        user_id=other_id,
        guest_id=None,
        provider="openai",
        model="gpt-4o-mini",
        prompt_tokens=500,
        completion_tokens=250,
        created_at=_NOW,
    )

    async with AsyncClient(
        transport=ASGITransport(app=observability_api_app),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/observability/usage",
            headers=_auth_headers(owner_id),
        )

    assert response.status_code == 200
    assert response.json()["rows"] == []


@pytest.mark.anyio
async def test_usage_summary_guest_scoped(
    db_session,
    observability_api_app: FastAPI,
) -> None:
    await _skip_without_usage_cost_columns(db_session)
    guest_id, guest_token = await _make_guest(db_session)
    await _seed_usage_event(
        db_session,
        user_id=None,
        guest_id=guest_id,
        provider="openai",
        model="gpt-4o-mini",
        prompt_tokens=12,
        completion_tokens=8,
        created_at=_NOW,
    )

    async with AsyncClient(
        transport=ASGITransport(app=observability_api_app),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/observability/usage",
            headers={"X-Guest-Token": guest_token},
        )

    assert response.status_code == 200
    assert len(response.json()["rows"]) == 1


@pytest.mark.anyio
async def test_usage_summary_invalid_date_range_returns_422(
    observability_api_app: FastAPI,
    db_session,
) -> None:
    await _skip_without_usage_cost_columns(db_session)
    user_id = await _make_user(db_session)

    async with AsyncClient(
        transport=ASGITransport(app=observability_api_app),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/observability/usage?since=2026-08-10&until=2026-08-01",
            headers=_auth_headers(user_id),
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.anyio
async def test_usage_summary_invalid_group_by_returns_422(
    observability_api_app: FastAPI,
    db_session,
) -> None:
    await _skip_without_usage_cost_columns(db_session)
    user_id = await _make_user(db_session)

    async with AsyncClient(
        transport=ASGITransport(app=observability_api_app),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/observability/usage?group_by=session",
            headers=_auth_headers(user_id),
        )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_usage_endpoint_disabled_returns_503(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OBSERVABILITY_ENABLED", "false")
    get_settings.cache_clear()
    test_app = _build_test_app()
    _bind_db_session(test_app, db_session)
    user_id = await _make_user(db_session)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=test_app),
            base_url="http://testserver",
        ) as client:
            response = await client.get(
                "/api/observability/usage",
                headers=_auth_headers(user_id),
            )
    finally:
        _clear_router_overrides(test_app)
        get_settings.cache_clear()

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "feature_disabled"


@pytest.mark.anyio
async def test_metrics_disabled_returns_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OBSERVABILITY_ENABLED", "false")
    get_settings.cache_clear()
    test_app = _build_test_app()

    try:
        async with AsyncClient(
            transport=ASGITransport(app=test_app),
            base_url="http://testserver",
        ) as client:
            response = await client.get("/metrics")
    finally:
        get_settings.cache_clear()

    assert response.status_code == 404


@pytest.mark.anyio
async def test_metrics_returns_prometheus_text_without_owner_labels(
    observability_api_app: FastAPI,
) -> None:
    from app.ai.observability.metrics.instruments import record_llm_request_metrics

    record_llm_request_metrics(
        provider="openai",
        model="gpt-4o-mini",
        succeeded=True,
        total_tokens=15,
    )

    async with AsyncClient(
        transport=ASGITransport(app=observability_api_app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    assert "# HELP" in body
    assert "# TYPE" in body
    for forbidden in ("user_id", "guest_id", "session_id", "trace_id", "span_id"):
        assert forbidden not in body


@pytest.mark.anyio
async def test_health_reports_observability_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OBSERVABILITY_ENABLED", "true")
    get_settings.cache_clear()
    test_app = _build_test_app(include_health=True)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=test_app),
            base_url="http://testserver",
        ) as client:
            response = await client.get("/api/health")
    finally:
        get_settings.cache_clear()

    assert response.status_code == 200
    assert response.json()["observability_enabled"] is True


@pytest.mark.anyio
async def test_usage_store_record_visible_via_api(
    db_session,
    observability_api_app: FastAPI,
) -> None:
    await _skip_without_usage_cost_columns(db_session)
    _init_observability_registries()
    user_id = await _make_user(db_session)
    chat_store = SqlChatStore(db_session)
    usage_store = SqlUsageStore(db_session)
    chat = await chat_store.create_session(user_id=user_id)
    await usage_store.record(
        session_id=chat.id,
        user_id=user_id,
        provider="openai",
        model="gpt-4o-mini",
        token_source="provider_reported",
        prompt_tokens=1000,
        completion_tokens=500,
        total_tokens=1500,
    )

    async with AsyncClient(
        transport=ASGITransport(app=observability_api_app),
        base_url="http://testserver",
    ) as client:
        response = await client.get(
            "/api/observability/usage?group_by=provider",
            headers=_auth_headers(user_id),
        )

    assert response.status_code == 200
    rows = response.json()["rows"]
    assert len(rows) == 1
    assert rows[0]["provider"] == "openai"
    assert rows[0]["request_count"] == 1
    assert rows[0]["total_tokens"] == 1500
    assert rows[0]["cost_usd"] is not None
