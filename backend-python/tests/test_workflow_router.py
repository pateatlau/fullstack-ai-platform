"""Workflow REST API integration tests (Epic 06 Phase 9)."""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Iterator

import pytest
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI

from app.ai.deps import get_workflow_manager
from app.ai.workflow.manager import WorkflowManager
from app.ai.workflow.models import (
    DefinitionStatus,
    NodeStatus,
    NodeType,
    RunStatus,
    WorkflowContext,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
    WorkflowNodeExecution,
    WorkflowRun,
)
from app.schemas.workflow import DEFAULT_WORKFLOW_LIST_LIMIT
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.security import create_access_token
from app.db.identity import SqlUserStore
from app.db.session import get_db_session
from app.routers import workflows as workflows_router
from tests.ai.workflow.test_interfaces import FakeWorkflowStore

_NOW = datetime.datetime.now(datetime.UTC)

_WORKFLOW_ROUTES = [
    ("POST", "/api/workflows"),
    ("GET", "/api/workflows"),
    ("GET", f"/api/workflows/{uuid.uuid4()}"),
    ("PUT", f"/api/workflows/{uuid.uuid4()}"),
    ("DELETE", f"/api/workflows/{uuid.uuid4()}"),
    ("POST", f"/api/workflows/{uuid.uuid4()}/runs"),
    ("GET", f"/api/workflows/{uuid.uuid4()}/runs"),
    ("GET", "/api/workflow-runs"),
    ("GET", f"/api/workflow-runs/{uuid.uuid4()}"),
    ("POST", f"/api/workflow-runs/{uuid.uuid4()}/cancel"),
    ("POST", f"/api/workflow-runs/{uuid.uuid4()}/resume"),
    (
        "POST",
        f"/api/workflow-runs/{uuid.uuid4()}/nodes/{uuid.uuid4()}/approve",
    ),
    (
        "POST",
        f"/api/workflow-runs/{uuid.uuid4()}/nodes/{uuid.uuid4()}/reject",
    ),
]


def _workflow_settings() -> Settings:
    return Settings(openai_api_key="test-key", workflow_engine_enabled=True)


def _auth_headers(user_id: uuid.UUID) -> dict[str, str]:
    token = create_access_token(user_id=user_id, settings=get_settings())
    return {"Authorization": f"Bearer {token}"}


def _definition_payload(*, status: str = "active") -> dict[str, object]:
    return {
        "name": "Sample Workflow",
        "status": status,
        "entry_node_id": "start",
        "nodes": [
            {"id": "start", "type": "task", "config": {}},
            {"id": "end", "type": "terminal", "config": {}},
        ],
        "edges": [{"id": "e1", "from_node_id": "start", "to_node_id": "end"}],
    }


def _valid_definition(owner_id: uuid.UUID) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=uuid.uuid4(),
        owner_id=owner_id,
        name="Sample Workflow",
        status=DefinitionStatus.ACTIVE,
        entry_node_id="start",
        nodes=[
            WorkflowNode(id="start", type=NodeType.TASK, config={}),
            WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
        ],
        edges=[WorkflowEdge(id="e1", from_node_id="start", to_node_id="end")],
        created_at=_NOW,
        updated_at=_NOW,
    )


def _build_test_app() -> FastAPI:
    test_app = FastAPI()
    test_app.include_router(workflows_router.router)
    register_exception_handlers(test_app)
    return test_app


def _bind_db_session(test_app: FastAPI, session) -> None:
    async def _override_db_session():
        yield session

    test_app.dependency_overrides[get_db_session] = _override_db_session


def _clear_router_overrides(test_app: FastAPI) -> None:
    test_app.dependency_overrides.pop(get_workflow_manager, None)
    test_app.dependency_overrides.pop(get_db_session, None)


async def _make_user(session) -> uuid.UUID:
    user = await SqlUserStore(session).create(
        sub=f"workflow-api-{uuid.uuid4()}",
        email=None,
        name=None,
        picture=None,
    )
    return user.id


def _disabled_route_request_json(method: str, path: str) -> dict[str, object]:
    if method in {"POST", "PUT"} and path.startswith("/api/workflows"):
        if path.endswith("/runs"):
            return {"idempotency_key": "disabled-key", "trigger_input": {}}
        if "/runs" not in path:
            return _definition_payload()
    return {}


@pytest.fixture
def workflow_api_app(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[FastAPI]:
    monkeypatch.setenv("WORKFLOW_ENGINE_ENABLED", "true")
    get_settings.cache_clear()
    test_app = _build_test_app()
    _bind_db_session(test_app, db_session)
    try:
        yield test_app
    finally:
        _clear_router_overrides(test_app)
        get_settings.cache_clear()


@pytest.mark.anyio
@pytest.mark.parametrize(("method", "path"), _WORKFLOW_ROUTES)
async def test_workflow_api_disabled_returns_503(
    db_session,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
) -> None:
    user_id = await _make_user(db_session)
    headers = _auth_headers(user_id)
    monkeypatch.setenv("WORKFLOW_ENGINE_ENABLED", "false")
    get_settings.cache_clear()
    test_app = _build_test_app()
    _bind_db_session(test_app, db_session)

    async with AsyncClient(
        transport=ASGITransport(app=test_app),
        base_url="http://testserver",
    ) as client:
        response = await client.request(
            method,
            path,
            headers=headers,
            json=_disabled_route_request_json(method, path),
        )

    get_settings.cache_clear()
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "feature_disabled"
    assert body["error"]["message"] == "Workflow Engine is not enabled on this server."


@pytest.mark.anyio
async def test_workflow_api_requires_authentication(
    workflow_api_app: FastAPI,
) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=workflow_api_app),
        base_url="http://testserver",
    ) as client:
        response = await client.get("/api/workflows")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


@pytest.mark.anyio
async def test_create_list_get_update_archive_definition(
    workflow_api_app: FastAPI,
) -> None:
    store = FakeWorkflowStore()
    owner_id = uuid.uuid4()
    workflow_api_app.dependency_overrides[get_workflow_manager] = lambda: (
        WorkflowManager(store)
    )
    headers = _auth_headers(owner_id)

    async with AsyncClient(
        transport=ASGITransport(app=workflow_api_app),
        base_url="http://testserver",
    ) as client:
        create_response = await client.post(
            "/api/workflows",
            headers=headers,
            json=_definition_payload(),
        )
        assert create_response.status_code == 200
        created = create_response.json()
        definition_id = created["id"]
        assert created["name"] == "Sample Workflow"
        assert created["version"] == 1
        assert "owner_id" not in created

        list_response = await client.get("/api/workflows", headers=headers)
        assert list_response.status_code == 200
        list_body = list_response.json()
        assert len(list_body["definitions"]) == 1
        assert list_body["limit"] == DEFAULT_WORKFLOW_LIST_LIMIT
        assert list_body["offset"] == 0
        assert list_body["total"] == 1

        get_response = await client.get(
            f"/api/workflows/{definition_id}",
            headers=headers,
        )
        assert get_response.status_code == 200
        assert get_response.json()["id"] == definition_id

        update_response = await client.put(
            f"/api/workflows/{definition_id}",
            headers=headers,
            json={
                **_definition_payload(),
                "name": "Renamed Workflow",
            },
        )
        assert update_response.status_code == 200
        assert update_response.json()["name"] == "Renamed Workflow"

        archive_response = await client.delete(
            f"/api/workflows/{definition_id}",
            headers=headers,
        )
        assert archive_response.status_code == 200
        assert archive_response.json()["status"] == "archived"


@pytest.mark.anyio
async def test_create_definition_validation_error_returns_422(
    workflow_api_app: FastAPI,
) -> None:
    store = FakeWorkflowStore()
    owner_id = uuid.uuid4()
    workflow_api_app.dependency_overrides[get_workflow_manager] = lambda: (
        WorkflowManager(store)
    )
    headers = _auth_headers(owner_id)
    invalid_payload = {
        "name": "Cycle Workflow",
        "status": "active",
        "entry_node_id": "a",
        "nodes": [
            {"id": "a", "type": "task", "config": {}},
            {"id": "b", "type": "task", "config": {}},
        ],
        "edges": [
            {"id": "e1", "from_node_id": "a", "to_node_id": "b"},
            {"id": "e2", "from_node_id": "b", "to_node_id": "a"},
        ],
    }

    async with AsyncClient(
        transport=ASGITransport(app=workflow_api_app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/workflows",
            headers=headers,
            json=invalid_payload,
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "workflow_validation_error"


@pytest.mark.anyio
async def test_create_definition_blank_name_returns_422(
    workflow_api_app: FastAPI,
) -> None:
    store = FakeWorkflowStore()
    owner_id = uuid.uuid4()
    workflow_api_app.dependency_overrides[get_workflow_manager] = lambda: (
        WorkflowManager(store)
    )
    headers = _auth_headers(owner_id)
    payload = _definition_payload()
    payload["name"] = "   "

    async with AsyncClient(
        transport=ASGITransport(app=workflow_api_app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/api/workflows",
            headers=headers,
            json=payload,
        )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "workflow_validation_error"
    assert "name must not be blank" in response.json()["error"]["message"]


@pytest.mark.anyio
async def test_owner_isolation_returns_404(
    workflow_api_app: FastAPI,
) -> None:
    store = FakeWorkflowStore()
    owner_id = uuid.uuid4()
    other_owner_id = uuid.uuid4()
    definition = await store.create_definition(_valid_definition(owner_id))
    run = await store.create_run(
        WorkflowRun(
            id=uuid.uuid4(),
            workflow_definition_id=definition.id,
            owner_id=owner_id,
            idempotency_key="key-1",
            status=RunStatus.RUNNING,
            context=WorkflowContext(),
            created_at=_NOW,
            updated_at=_NOW,
        )
    )
    workflow_api_app.dependency_overrides[get_workflow_manager] = lambda: (
        WorkflowManager(store)
    )
    headers = _auth_headers(other_owner_id)

    async with AsyncClient(
        transport=ASGITransport(app=workflow_api_app),
        base_url="http://testserver",
    ) as client:
        definition_response = await client.get(
            f"/api/workflows/{definition.id}",
            headers=headers,
        )
        run_response = await client.get(
            f"/api/workflow-runs/{run.id}",
            headers=headers,
        )

    assert definition_response.status_code == 404
    assert run_response.status_code == 404


@pytest.mark.anyio
async def test_start_run_idempotency_returns_existing_run(
    workflow_api_app: FastAPI,
) -> None:
    store = FakeWorkflowStore()
    owner_id = uuid.uuid4()
    definition = await store.create_definition(_valid_definition(owner_id))
    manager = WorkflowManager(store)
    workflow_api_app.dependency_overrides[get_workflow_manager] = lambda: manager
    headers = _auth_headers(owner_id)
    body = {"idempotency_key": "launch-1", "trigger_input": {"topic": "test"}}

    async with AsyncClient(
        transport=ASGITransport(app=workflow_api_app),
        base_url="http://testserver",
    ) as client:
        first = await client.post(
            f"/api/workflows/{definition.id}/runs",
            headers=headers,
            json=body,
        )
        second = await client.post(
            f"/api/workflows/{definition.id}/runs",
            headers=headers,
            json=body,
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert "checkpoint_version" not in first.json()
    all_runs = await store.list_runs(
        owner_id=owner_id,
        workflow_definition_id=definition.id,
    )
    assert len(all_runs) == 1


@pytest.mark.anyio
async def test_list_runs_and_get_run_detail(
    workflow_api_app: FastAPI,
) -> None:
    store = FakeWorkflowStore()
    owner_id = uuid.uuid4()
    definition = await store.create_definition(_valid_definition(owner_id))
    run = await store.create_run(
        WorkflowRun(
            id=uuid.uuid4(),
            workflow_definition_id=definition.id,
            owner_id=owner_id,
            idempotency_key="key-1",
            status=RunStatus.RUNNING,
            context=WorkflowContext(trigger_input={"x": 1}),
            current_node_ids=["start"],
            checkpoint_version=3,
            created_at=_NOW,
            updated_at=_NOW,
            started_at=_NOW,
        )
    )
    execution = await store.append_node_execution(
        WorkflowNodeExecution(
            id=uuid.uuid4(),
            run_id=run.id,
            node_id="start",
            node_type=NodeType.TASK,
            attempt=2,
            status=NodeStatus.RUNNING,
            input={"execution_receipt_id": "receipt-1"},
            started_at=_NOW,
        )
    )
    workflow_api_app.dependency_overrides[get_workflow_manager] = lambda: (
        WorkflowManager(store)
    )
    headers = _auth_headers(owner_id)

    async with AsyncClient(
        transport=ASGITransport(app=workflow_api_app),
        base_url="http://testserver",
    ) as client:
        all_runs = await client.get("/api/workflow-runs", headers=headers)
        scoped_runs = await client.get(
            f"/api/workflows/{definition.id}/runs",
            headers=headers,
        )
        detail = await client.get(
            f"/api/workflow-runs/{run.id}",
            headers=headers,
        )

    assert all_runs.status_code == 200
    all_runs_body = all_runs.json()
    assert len(all_runs_body["runs"]) == 1
    assert all_runs_body["total"] == 1
    assert scoped_runs.status_code == 200
    scoped_runs_body = scoped_runs.json()
    assert len(scoped_runs_body["runs"]) == 1
    assert scoped_runs_body["total"] == 1
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["context"]["trigger_input"] == {"x": 1}
    assert "checkpoint_version" not in detail_body
    assert len(detail_body["node_executions"]) == 1
    assert detail_body["node_executions"][0]["id"] == str(execution.id)
    assert "attempt" not in detail_body["node_executions"][0]


@pytest.mark.anyio
async def test_cancel_and_resume_run(
    workflow_api_app: FastAPI,
) -> None:
    store = FakeWorkflowStore()
    owner_id = uuid.uuid4()
    definition = await store.create_definition(_valid_definition(owner_id))
    run = await store.create_run(
        WorkflowRun(
            id=uuid.uuid4(),
            workflow_definition_id=definition.id,
            owner_id=owner_id,
            idempotency_key="key-1",
            status=RunStatus.RUNNING,
            context=WorkflowContext(),
            current_node_ids=["start"],
            checkpoint_version=0,
            created_at=_NOW,
            updated_at=_NOW,
            started_at=_NOW,
        )
    )
    workflow_api_app.dependency_overrides[get_workflow_manager] = lambda: (
        WorkflowManager(store)
    )
    headers = _auth_headers(owner_id)

    async with AsyncClient(
        transport=ASGITransport(app=workflow_api_app),
        base_url="http://testserver",
    ) as client:
        cancel_response = await client.post(
            f"/api/workflow-runs/{run.id}/cancel",
            headers=headers,
        )
        resume_response = await client.post(
            f"/api/workflow-runs/{run.id}/resume",
            headers=headers,
        )

    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "cancelled"
    assert resume_response.status_code == 200
    assert resume_response.json()["status"] == "cancelled"


@pytest.mark.anyio
async def test_approve_and_reject_pending_approval_node(
    workflow_api_app: FastAPI,
) -> None:
    store = FakeWorkflowStore()
    owner_id = uuid.uuid4()
    definition = await store.create_definition(
        WorkflowDefinition(
            id=uuid.uuid4(),
            owner_id=owner_id,
            name="Approval Workflow",
            status=DefinitionStatus.ACTIVE,
            entry_node_id="approve",
            nodes=[
                WorkflowNode(id="approve", type=NodeType.APPROVAL, config={}),
                WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
            ],
            edges=[WorkflowEdge(id="e1", from_node_id="approve", to_node_id="end")],
            created_at=_NOW,
            updated_at=_NOW,
        )
    )
    run = await store.create_run(
        WorkflowRun(
            id=uuid.uuid4(),
            workflow_definition_id=definition.id,
            owner_id=owner_id,
            idempotency_key="key-1",
            status=RunStatus.WAITING_APPROVAL,
            context=WorkflowContext(),
            current_node_ids=["approve"],
            checkpoint_version=1,
            created_at=_NOW,
            updated_at=_NOW,
            started_at=_NOW,
        )
    )
    execution = await store.append_node_execution(
        WorkflowNodeExecution(
            id=uuid.uuid4(),
            run_id=run.id,
            node_id="approve",
            node_type=NodeType.APPROVAL,
            status=NodeStatus.WAITING_APPROVAL,
            input={"prompt": "Approve?"},
            started_at=_NOW,
        )
    )
    manager = WorkflowManager(store)
    workflow_api_app.dependency_overrides[get_workflow_manager] = lambda: manager
    headers = _auth_headers(owner_id)

    async with AsyncClient(
        transport=ASGITransport(app=workflow_api_app),
        base_url="http://testserver",
    ) as client:
        approve_response = await client.post(
            f"/api/workflow-runs/{run.id}/nodes/{execution.id}/approve",
            headers=headers,
        )
        duplicate = await client.post(
            f"/api/workflow-runs/{run.id}/nodes/{execution.id}/approve",
            headers=headers,
        )

        reject_run = await store.create_run(
            WorkflowRun(
                id=uuid.uuid4(),
                workflow_definition_id=definition.id,
                owner_id=owner_id,
                idempotency_key="key-2",
                status=RunStatus.WAITING_APPROVAL,
                context=WorkflowContext(),
                current_node_ids=["approve"],
                checkpoint_version=1,
                created_at=_NOW,
                updated_at=_NOW,
                started_at=_NOW,
            )
        )
        reject_execution = await store.append_node_execution(
            WorkflowNodeExecution(
                id=uuid.uuid4(),
                run_id=reject_run.id,
                node_id="approve",
                node_type=NodeType.APPROVAL,
                status=NodeStatus.WAITING_APPROVAL,
                input={"prompt": "Approve?"},
                started_at=_NOW,
            )
        )
        reject_response = await client.post(
            f"/api/workflow-runs/{reject_run.id}/nodes/{reject_execution.id}/reject",
            headers=headers,
        )
        conflict_response = await client.post(
            f"/api/workflow-runs/{reject_run.id}/nodes/{reject_execution.id}/approve",
            headers=headers,
        )

    assert approve_response.status_code == 200
    assert duplicate.status_code == 200
    assert reject_response.status_code == 200
    assert conflict_response.status_code == 409
    assert conflict_response.json()["error"]["code"] == "workflow_decision_conflict"


@pytest.mark.anyio
async def test_reject_with_unknown_fields_returns_422(
    workflow_api_app: FastAPI,
) -> None:
    owner_id = uuid.uuid4()
    workflow_api_app.dependency_overrides[get_workflow_manager] = lambda: (
        WorkflowManager(FakeWorkflowStore())
    )
    headers = _auth_headers(owner_id)

    async with AsyncClient(
        transport=ASGITransport(app=workflow_api_app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            f"/api/workflow-runs/{uuid.uuid4()}/nodes/{uuid.uuid4()}/reject",
            headers=headers,
            json={"edited_arguments": {"message": "nope"}, "reason": "no"},
        )

    assert response.status_code == 422


@pytest.mark.anyio
async def test_list_endpoints_honor_limit_and_offset(workflow_api_app: FastAPI) -> None:
    store = FakeWorkflowStore()
    owner_id = uuid.uuid4()
    for index in range(3):
        await store.create_definition(
            WorkflowDefinition(
                id=uuid.uuid4(),
                owner_id=owner_id,
                name=f"Workflow {index}",
                status=DefinitionStatus.ACTIVE,
                entry_node_id="start",
                nodes=[
                    WorkflowNode(id="start", type=NodeType.TASK, config={}),
                    WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
                ],
                edges=[WorkflowEdge(id="e1", from_node_id="start", to_node_id="end")],
                created_at=_NOW,
                updated_at=_NOW,
            )
        )
    definition = next(
        definition
        for definition in store._definitions.values()
        if definition.owner_id == owner_id
    )
    for index in range(3):
        await store.create_run(
            WorkflowRun(
                id=uuid.uuid4(),
                workflow_definition_id=definition.id,
                owner_id=owner_id,
                idempotency_key=f"run-{index}",
                status=RunStatus.COMPLETED,
                context=WorkflowContext(),
                created_at=_NOW + datetime.timedelta(seconds=index),
                updated_at=_NOW + datetime.timedelta(seconds=index),
            )
        )
    workflow_api_app.dependency_overrides[get_workflow_manager] = lambda: (
        WorkflowManager(store)
    )
    headers = _auth_headers(owner_id)

    async with AsyncClient(
        transport=ASGITransport(app=workflow_api_app),
        base_url="http://testserver",
    ) as client:
        definitions_page = await client.get(
            "/api/workflows",
            headers=headers,
            params={"limit": 2, "offset": 1},
        )
        runs_page = await client.get(
            "/api/workflow-runs",
            headers=headers,
            params={"limit": 1, "offset": 1},
        )
        scoped_runs_page = await client.get(
            f"/api/workflows/{definition.id}/runs",
            headers=headers,
            params={"limit": 1, "offset": 0},
        )

    definitions_body = definitions_page.json()
    assert definitions_page.status_code == 200
    assert len(definitions_body["definitions"]) == 2
    assert definitions_body["total"] == 3
    assert definitions_body["limit"] == 2
    assert definitions_body["offset"] == 1

    runs_body = runs_page.json()
    assert runs_page.status_code == 200
    assert len(runs_body["runs"]) == 1
    assert runs_body["total"] == 3
    assert runs_body["limit"] == 1
    assert runs_body["offset"] == 1

    scoped_body = scoped_runs_page.json()
    assert scoped_runs_page.status_code == 200
    assert len(scoped_body["runs"]) == 1
    assert scoped_body["total"] == 3
