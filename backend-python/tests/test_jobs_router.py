"""Background Jobs REST API integration tests (Epic 10 Phase 7)."""

from __future__ import annotations

import base64
import json
import datetime
import uuid
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.ai.deps import get_job_queue, get_job_schedule_store
from app.ai.jobs.models import BackgroundJob, JobResult, ScheduleStatus
from app.ai.jobs.queue import PostgresJobQueue
from app.ai.jobs.schedule_store import PostgresJobScheduleStore
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.security import create_access_token
from app.db.identity import SqlUserStore
from app.db.session import get_db_session
from app.routers import health as health_router
from app.routers import jobs as jobs_router
from tests.ai.jobs.conftest import (
    background_jobs_table_available,
    make_queue_session_factory,
)

_JOBS_ROUTES = [
    ("GET", "/api/jobs"),
    ("GET", "/api/jobs/schedules"),
    ("GET", "/api/jobs/00000000-0000-4000-8000-000000000001"),
    ("POST", "/api/jobs/00000000-0000-4000-8000-000000000001/retry"),
]


def _auth_headers(user_id: uuid.UUID) -> dict[str, str]:
    token = create_access_token(user_id=user_id, settings=get_settings())
    return {"Authorization": f"Bearer {token}"}


def _job_settings() -> Settings:
    return Settings(
        openai_api_key="test-key",
        background_jobs_enabled=True,
        background_jobs_default_max_attempts=3,
        background_jobs_claim_lease_seconds=300,
    )


def _build_test_app(*, include_health: bool = False) -> FastAPI:
    test_app = FastAPI()
    test_app.include_router(jobs_router.router)
    if include_health:
        test_app.include_router(health_router.router)
    register_exception_handlers(test_app)
    return test_app


def _bind_db_session(test_app: FastAPI, session) -> None:
    async def _override_db_session():
        yield session

    test_app.dependency_overrides[get_db_session] = _override_db_session


def _bind_jobs_dependencies(
    test_app: FastAPI,
    *,
    session_factory,
    settings: Settings,
) -> tuple[PostgresJobQueue, PostgresJobScheduleStore]:
    queue = PostgresJobQueue(session_factory, settings)
    schedule_store = PostgresJobScheduleStore(session_factory)

    def _override_queue():
        return queue

    def _override_schedule_store():
        return schedule_store

    test_app.dependency_overrides[get_job_queue] = _override_queue
    test_app.dependency_overrides[get_job_schedule_store] = _override_schedule_store
    return queue, schedule_store


def _clear_router_overrides(test_app: FastAPI) -> None:
    test_app.dependency_overrides.pop(get_job_queue, None)
    test_app.dependency_overrides.pop(get_job_schedule_store, None)
    test_app.dependency_overrides.pop(get_db_session, None)


async def _make_user(session) -> uuid.UUID:
    user = await SqlUserStore(session).create(
        sub=f"jobs-api-{uuid.uuid4()}",
        email=None,
        name=None,
        picture=None,
    )
    return user.id


async def _claim_job(
    queue: PostgresJobQueue,
    job_id: uuid.UUID,
    *,
    worker_id: str = "test-worker",
) -> BackgroundJob:
    for _ in range(5):
        claimed = await queue.claim_due(
            worker_id=worker_id,
            batch_size=10,
            lease_seconds=300,
        )
        match = next((item for item in claimed if item.id == job_id), None)
        if match is not None:
            return match
    raise AssertionError(f"Failed to claim job {job_id}")


@pytest.fixture
def jobs_api_app(db_session, monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    monkeypatch.setenv("BACKGROUND_JOBS_ENABLED", "true")
    monkeypatch.setenv("SECURITY_GOVERNANCE_ENABLED", "false")
    get_settings.cache_clear()
    settings = _job_settings()
    factory = make_queue_session_factory(db_session.bind)
    test_app = _build_test_app()
    _bind_db_session(test_app, db_session)
    _bind_jobs_dependencies(test_app, session_factory=factory, settings=settings)
    try:
        yield test_app
    finally:
        _clear_router_overrides(test_app)
        get_settings.cache_clear()


@pytest.mark.anyio
@pytest.mark.parametrize(("method", "path"), _JOBS_ROUTES)
async def test_jobs_api_disabled_returns_503(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
) -> None:
    if not await background_jobs_table_available(db_session):
        pytest.skip("background_jobs not available — run alembic upgrade head")

    user_id = await _make_user(db_session)
    headers = _auth_headers(user_id)
    monkeypatch.setenv("BACKGROUND_JOBS_ENABLED", "false")
    get_settings.cache_clear()
    settings = Settings(openai_api_key="test-key", background_jobs_enabled=False)
    factory = make_queue_session_factory(db_session.bind)
    test_app = _build_test_app()
    _bind_db_session(test_app, db_session)
    _bind_jobs_dependencies(test_app, session_factory=factory, settings=settings)

    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://testserver",
    ) as client:
        response = await client.request(method, path, headers=headers)

    _clear_router_overrides(test_app)
    get_settings.cache_clear()
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "feature_disabled"
    assert body["error"]["message"] == "Background Jobs are not enabled on this server."


async def _grant_role(db_session, user_id: uuid.UUID, role_name: str) -> None:
    from app.ai.security.rbac.service import RbacService
    from app.ai.security.rbac.store import PostgresRoleStore

    await RbacService(PostgresRoleStore(db_session)).assign_role(user_id, role_name)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/jobs"),
        ("GET", "/api/jobs/schedules"),
        ("GET", "/api/jobs/00000000-0000-4000-8000-000000000001"),
        ("POST", "/api/jobs/00000000-0000-4000-8000-000000000001/retry"),
    ],
)
async def test_jobs_api_denies_member_only_user_when_rbac_enforced(
    db_session,
    jobs_api_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
) -> None:
    if not await background_jobs_table_available(db_session):
        pytest.skip("background_jobs not available — run alembic upgrade head")

    monkeypatch.setenv("SECURITY_GOVERNANCE_ENABLED", "true")
    monkeypatch.setenv("SECURITY_RBAC_ENFORCEMENT_ENABLED", "true")
    get_settings.cache_clear()
    user_id = await _make_user(db_session)
    headers = _auth_headers(user_id)

    async with AsyncClient(
        transport=ASGITransport(app=jobs_api_app),
        base_url="http://testserver",
    ) as client:
        response = await client.request(method, path, headers=headers)

    get_settings.cache_clear()
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_denied"


@pytest.mark.anyio
async def test_jobs_api_allows_operator_when_rbac_enforced(
    db_session,
    jobs_api_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not await background_jobs_table_available(db_session):
        pytest.skip("background_jobs not available — run alembic upgrade head")

    monkeypatch.setenv("SECURITY_GOVERNANCE_ENABLED", "true")
    monkeypatch.setenv("SECURITY_RBAC_ENFORCEMENT_ENABLED", "true")
    get_settings.cache_clear()
    user_id = await _make_user(db_session)
    await _grant_role(db_session, user_id, "operator")
    headers = _auth_headers(user_id)

    async with AsyncClient(
        transport=ASGITransport(app=jobs_api_app),
        base_url="http://testserver",
    ) as client:
        list_response = await client.get("/api/jobs", headers=headers)

    get_settings.cache_clear()
    assert list_response.status_code == 200


@pytest.mark.anyio
async def test_jobs_api_requires_authentication(jobs_api_app: FastAPI) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=jobs_api_app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/jobs")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


@pytest.mark.anyio
async def test_list_jobs_supports_filters_and_pagination(
    db_session,
    jobs_api_app: FastAPI,
) -> None:
    user_id = await _make_user(db_session)
    headers = _auth_headers(user_id)
    queue = jobs_api_app.dependency_overrides[get_job_queue]()

    await queue.enqueue(
        job_type="fixture_success",
        payload={"version": 1},
    )
    await queue.enqueue(
        job_type="fixture_other",
        payload={"version": 1},
    )
    dead_job = await queue.enqueue(
        job_type="fixture_success",
        payload={"version": 1},
    )
    claimed = await _claim_job(queue, dead_job.id)
    await queue.fail(
        dead_job.id,
        error="permanent",
        expected_version=claimed.version,
        dead_letter=True,
    )

    async with AsyncClient(
        transport=ASGITransport(app=jobs_api_app),
        base_url="http://testserver",
    ) as client:
        filtered = await client.get(
            "/api/jobs",
            headers=headers,
            params={"status": "dead_letter", "job_type": "fixture_success", "limit": 1},
        )
        paged = await client.get(
            "/api/jobs",
            headers=headers,
            params={"limit": 1, "offset": 1},
        )

    assert filtered.status_code == 200
    filtered_body = filtered.json()
    assert len(filtered_body["jobs"]) == 1
    assert filtered_body["jobs"][0]["status"] == "dead_letter"
    assert filtered_body["jobs"][0]["job_type"] == "fixture_success"

    assert paged.status_code == 200
    assert len(paged.json()["jobs"]) == 1


@pytest.mark.anyio
async def test_get_job_detail_returns_job_or_404(
    db_session,
    jobs_api_app: FastAPI,
) -> None:
    user_id = await _make_user(db_session)
    headers = _auth_headers(user_id)
    queue = jobs_api_app.dependency_overrides[get_job_queue]()
    job = await queue.enqueue(
        job_type="fixture_success",
        payload={"version": 1, "document_id": str(uuid.uuid4())},
    )
    missing_id = uuid.uuid4()

    async with AsyncClient(
        transport=ASGITransport(app=jobs_api_app),
        base_url="http://testserver",
    ) as client:
        found = await client.get(f"/api/jobs/{job.id}", headers=headers)
        missing = await client.get(f"/api/jobs/{missing_id}", headers=headers)

    assert found.status_code == 200
    assert found.json()["id"] == str(job.id)
    assert found.json()["job_type"] == "fixture_success"

    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "job_not_found"


@pytest.mark.anyio
async def test_retry_dead_letter_resets_job(
    db_session,
    jobs_api_app: FastAPI,
) -> None:
    user_id = await _make_user(db_session)
    headers = _auth_headers(user_id)
    queue = jobs_api_app.dependency_overrides[get_job_queue]()
    job = await queue.enqueue(
        job_type="fixture_success",
        payload={"version": 1},
    )
    claimed = await _claim_job(queue, job.id)
    await queue.fail(
        job.id,
        error="boom",
        expected_version=claimed.version,
        dead_letter=True,
    )

    async with AsyncClient(
        transport=ASGITransport(app=jobs_api_app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(f"/api/jobs/{job.id}/retry", headers=headers)

    assert response.status_code == 200
    body = response.json()["job"]
    assert body["status"] == "queued"
    assert body["attempt_count"] == 0
    assert body["last_error"] is None


@pytest.mark.anyio
async def test_retry_non_dead_letter_returns_409(
    db_session,
    jobs_api_app: FastAPI,
) -> None:
    user_id = await _make_user(db_session)
    headers = _auth_headers(user_id)
    queue = jobs_api_app.dependency_overrides[get_job_queue]()
    job = await queue.enqueue(
        job_type="fixture_success",
        payload={"version": 1},
    )

    async with AsyncClient(
        transport=ASGITransport(app=jobs_api_app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(f"/api/jobs/{job.id}/retry", headers=headers)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "job_not_retryable"


@pytest.mark.anyio
async def test_list_schedules_returns_schedule_rows(
    db_session,
    jobs_api_app: FastAPI,
) -> None:
    user_id = await _make_user(db_session)
    headers = _auth_headers(user_id)
    schedule_store = jobs_api_app.dependency_overrides[get_job_schedule_store]()
    await schedule_store.insert_schedule(
        name=f"router-test-{uuid.uuid4()}",
        job_type="fixture_success",
        payload={"version": 1},
        interval_seconds=300,
        next_run_at=datetime.datetime.now(datetime.UTC),
        status=ScheduleStatus.ENABLED,
    )

    async with AsyncClient(
        transport=ASGITransport(app=jobs_api_app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/jobs/schedules", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert len(body["schedules"]) >= 1
    schedule = body["schedules"][0]
    assert "name" in schedule
    assert "job_type" in schedule
    assert "interval_seconds" in schedule
    assert "next_run_at" in schedule
    assert "status" in schedule


@pytest.mark.anyio
async def test_job_responses_redact_sensitive_payload_and_result_fields(
    db_session,
    jobs_api_app: FastAPI,
) -> None:
    user_id = await _make_user(db_session)
    headers = _auth_headers(user_id)
    queue = jobs_api_app.dependency_overrides[get_job_queue]()
    secret_bytes = base64.b64encode(b"raw-file-bytes").decode("ascii")
    job = await queue.enqueue(
        job_type="rag_document_indexing",
        payload={
            "version": 1,
            "document_id": str(uuid.uuid4()),
            "user_id": str(uuid.uuid4()),
            "api_key": "sk-live-secret-value",
            "tool_arguments": {"query": "secret search"},
            "file_bytes": secret_bytes,
            "metadata": {"team": "ops"},
            "storage_path": "/var/data/uploads/secret.pdf",
        },
    )
    claimed = await _claim_job(queue, job.id)
    await queue.complete(
        job.id,
        result=JobResult(
            summary="indexed",
            counts={"chunks": 3},
            ref_id="/tmp/reports/scheduled-eval.json",
        ),
        expected_version=claimed.version,
    )

    async with AsyncClient(
        transport=ASGITransport(app=jobs_api_app),
        base_url="http://testserver",
    ) as client:
        detail = await client.get(f"/api/jobs/{job.id}", headers=headers)
        listed = await client.get("/api/jobs", headers=headers)

    assert detail.status_code == 200
    assert listed.status_code == 200

    serialized = json.dumps(detail.json()) + json.dumps(listed.json())
    body = detail.json()
    assert body["payload"] == {
        "version": 1,
        "document_id": job.payload["document_id"],
        "user_id": job.payload["user_id"],
    }
    assert body["result"] == {"summary": "indexed", "counts": {"chunks": 3}}
    assert "api_key" not in body["payload"]
    assert "tool_arguments" not in serialized
    assert "file_bytes" not in serialized
    assert "metadata" not in serialized
    assert "storage_path" not in serialized
    assert "sk-live-secret-value" not in serialized
    assert secret_bytes not in serialized
    assert "/tmp/reports" not in serialized
    assert "ref_id" not in serialized


@pytest.mark.anyio
async def test_health_includes_background_jobs_fields_when_enabled(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not await background_jobs_table_available(db_session):
        pytest.skip("background_jobs not available — run alembic upgrade head")

    monkeypatch.setenv("BACKGROUND_JOBS_ENABLED", "true")
    get_settings.cache_clear()
    settings = _job_settings()
    factory = make_queue_session_factory(db_session.bind)
    queue = PostgresJobQueue(factory, settings)
    await queue.enqueue(job_type="fixture_success", payload={"version": 1})
    dead_job = await queue.enqueue(
        job_type="fixture_success",
        payload={"version": 1},
    )
    claimed = await _claim_job(queue, dead_job.id, worker_id="health-worker")
    await queue.fail(
        dead_job.id,
        error="failed",
        expected_version=claimed.version,
        dead_letter=True,
    )

    test_app = _build_test_app(include_health=True)
    _bind_db_session(test_app, db_session)
    _bind_jobs_dependencies(test_app, session_factory=factory, settings=settings)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=test_app),
            base_url="http://testserver",
        ) as client:
            response = await client.get("/api/health")
    finally:
        _clear_router_overrides(test_app)
        get_settings.cache_clear()

    assert response.status_code == 200
    body = response.json()
    assert body["background_jobs_enabled"] is True
    assert body["background_jobs_pending_count"] >= 1
    assert body["background_jobs_dead_letter_count"] >= 1


@pytest.mark.anyio
async def test_health_background_jobs_counts_zero_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BACKGROUND_JOBS_ENABLED", "false")
    get_settings.cache_clear()
    test_app = _build_test_app(include_health=True)
    register_exception_handlers(test_app)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=test_app),
            base_url="http://testserver",
        ) as client:
            response = await client.get("/api/health")
    finally:
        get_settings.cache_clear()

    assert response.status_code == 200
    body = response.json()
    assert body["background_jobs_enabled"] is False
    assert body["background_jobs_pending_count"] == 0
    assert body["background_jobs_dead_letter_count"] == 0
