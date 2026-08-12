"""Workflow run retention cleanup handler tests (Epic 10 Phase 4)."""

from __future__ import annotations

import datetime
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select, text, update

from app.ai.jobs.handlers import workflow_retention as retention_module
from app.ai.jobs.handlers.workflow_retention import workflow_run_retention_cleanup
from app.ai.jobs.models import BackgroundJob, JobStatus
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
from app.ai.workflow.providers.postgres import PostgresWorkflowStore
from app.core.config import Settings
from app.db.documents import SqlDocumentStore
from app.db.identity import SqlUserStore
from app.db.models import (
    DocumentUploadStaging,
    WorkflowNodeExecutionRecord,
    WorkflowRunRecord,
)
from tests.ai.jobs.conftest import (
    background_jobs_table_available,
    make_queue_session_factory,
)


async def _workflow_tables_available(session) -> bool:
    result = await session.scalar(
        text("SELECT to_regclass('public.workflow_runs') IS NOT NULL")
    )
    return bool(result)


async def _upload_staging_table_available(session) -> bool:
    result = await session.scalar(
        text("SELECT to_regclass('public.document_upload_staging') IS NOT NULL")
    )
    return bool(result)


@pytest.fixture(autouse=True)
async def _truncate_workflow_runs_for_retention_tests(db_session):
    async def _cleanup() -> None:
        if await _upload_staging_table_available(db_session):
            await db_session.execute(
                text("TRUNCATE document_upload_staging RESTART IDENTITY CASCADE")
            )
        if await _workflow_tables_available(db_session):
            await db_session.execute(
                text("TRUNCATE workflow_runs RESTART IDENTITY CASCADE")
            )
        if await background_jobs_table_available(db_session):
            await db_session.execute(text("TRUNCATE background_jobs"))
        await db_session.commit()

    await _cleanup()
    yield
    await _cleanup()


def _sample_job(*, job_id: uuid.UUID | None = None) -> BackgroundJob:
    now = datetime.datetime.now(datetime.UTC)
    return BackgroundJob(
        id=job_id or uuid.uuid4(),
        job_type="workflow_run_retention_cleanup",
        status=JobStatus.RUNNING,
        payload={"version": 1},
        attempt_count=1,
        max_attempts=3,
        version=1,
        run_at=now,
        created_at=now,
        updated_at=now,
        locked_by="test-worker",
        locked_at=now,
        started_at=now,
    )


def _retention_settings(**overrides: object) -> Settings:
    base = {
        "openai_api_key": "test-key",
        "background_jobs_enabled": True,
        "workflow_run_retention_days": 30,
        "background_jobs_retention_days": 30,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


async def _create_owner(db_session) -> uuid.UUID:
    user = await SqlUserStore(db_session).create(
        sub=f"retention-{uuid.uuid4()}",
        email=None,
        name=None,
        picture=None,
    )
    return user.id


async def _create_definition(
    db_session,
    *,
    owner_id: uuid.UUID,
) -> uuid.UUID:
    now = datetime.datetime.now(datetime.UTC)
    definition = WorkflowDefinition(
        id=uuid.uuid4(),
        owner_id=owner_id,
        name="retention-test",
        status=DefinitionStatus.ACTIVE,
        entry_node_id="start",
        nodes=[
            WorkflowNode(id="start", type=NodeType.TASK, config={}),
            WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
        ],
        edges=[WorkflowEdge(id="e1", from_node_id="start", to_node_id="end")],
        created_at=now,
        updated_at=now,
    )
    store = PostgresWorkflowStore(db_session, Settings(openai_api_key="test-key"))
    persisted = await store.create_definition(definition)
    return persisted.id


async def _create_run(
    db_session,
    *,
    owner_id: uuid.UUID,
    definition_id: uuid.UUID,
    status: RunStatus,
    updated_at: datetime.datetime | None = None,
) -> uuid.UUID:
    now = datetime.datetime.now(datetime.UTC)
    store = PostgresWorkflowStore(db_session, Settings(openai_api_key="test-key"))
    run = await store.create_run(
        WorkflowRun(
            id=uuid.uuid4(),
            workflow_definition_id=definition_id,
            owner_id=owner_id,
            idempotency_key=f"key-{uuid.uuid4().hex}",
            status=status,
            context=WorkflowContext(trigger_input={}),
            current_node_ids=["start"],
            checkpoint_version=1,
            created_at=now,
            updated_at=now,
            started_at=now,
        )
    )
    if updated_at is not None:
        await db_session.execute(
            update(WorkflowRunRecord)
            .where(WorkflowRunRecord.id == run.id)
            .values(updated_at=updated_at)
        )
    await db_session.commit()
    return run.id


async def _append_execution(db_session, *, run_id: uuid.UUID) -> uuid.UUID:
    now = datetime.datetime.now(datetime.UTC)
    store = PostgresWorkflowStore(db_session, Settings(openai_api_key="test-key"))
    execution = await store.append_node_execution(
        WorkflowNodeExecution(
            id=uuid.uuid4(),
            run_id=run_id,
            node_id="start",
            node_type=NodeType.TASK,
            attempt=1,
            status=NodeStatus.SUCCEEDED,
            input={},
            started_at=now,
            completed_at=now,
        )
    )
    await db_session.commit()
    return execution.id


async def _insert_terminal_background_job(
    db_session,
    *,
    updated_at: datetime.datetime,
    status: str = "succeeded",
    job_id: uuid.UUID | None = None,
) -> uuid.UUID:
    job_id = job_id or uuid.uuid4()
    await db_session.execute(
        text(
            """
            INSERT INTO background_jobs (
                id, job_type, status, payload, attempt_count, max_attempts,
                version, run_at, created_at, updated_at, finished_at
            ) VALUES (
                :id, 'fixture', :status, '{}'::jsonb, 1, 3,
                1, :updated_at, :updated_at, :updated_at, :updated_at
            )
            """
        ),
        {"id": job_id, "status": status, "updated_at": updated_at},
    )
    await db_session.commit()
    return job_id


@pytest.mark.anyio
async def test_retention_disabled_when_background_jobs_flag_off() -> None:
    result = await workflow_run_retention_cleanup(
        _sample_job(),
        settings=Settings(
            openai_api_key="test-key",
            background_jobs_enabled=False,
        ),
        session_factory=None,  # type: ignore[arg-type]
    )
    assert result.counts["workflow_runs_deleted"] == 0
    assert result.counts["background_jobs_deleted"] == 0
    assert result.counts["upload_staging_deleted"] == 0


@pytest.mark.anyio
async def test_deletes_old_terminal_workflow_run_and_cascades_executions(
    db_session,
) -> None:
    if not await _workflow_tables_available(db_session):
        pytest.skip("workflow tables not available — run alembic upgrade head")

    owner_id = await _create_owner(db_session)
    definition_id = await _create_definition(db_session, owner_id=owner_id)
    stale_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=45)
    run_id = await _create_run(
        db_session,
        owner_id=owner_id,
        definition_id=definition_id,
        status=RunStatus.COMPLETED,
        updated_at=stale_at,
    )
    execution_id = await _append_execution(db_session, run_id=run_id)

    settings = _retention_settings()
    factory = make_queue_session_factory(db_session.bind)
    result = await workflow_run_retention_cleanup(
        _sample_job(),
        settings=settings,
        session_factory=factory,
    )

    assert result.counts["workflow_runs_deleted"] == 1
    async with factory() as session:
        assert await session.get(WorkflowRunRecord, run_id) is None
        assert await session.get(WorkflowNodeExecutionRecord, execution_id) is None


@pytest.mark.anyio
async def test_keeps_terminal_run_within_retention_window(db_session) -> None:
    if not await _workflow_tables_available(db_session):
        pytest.skip("workflow tables not available — run alembic upgrade head")

    owner_id = await _create_owner(db_session)
    definition_id = await _create_definition(db_session, owner_id=owner_id)
    recent_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=10)
    run_id = await _create_run(
        db_session,
        owner_id=owner_id,
        definition_id=definition_id,
        status=RunStatus.FAILED,
        updated_at=recent_at,
    )

    factory = make_queue_session_factory(db_session.bind)
    result = await workflow_run_retention_cleanup(
        _sample_job(),
        settings=_retention_settings(),
        session_factory=factory,
    )

    assert result.counts["workflow_runs_deleted"] == 0
    async with factory() as session:
        assert await session.get(WorkflowRunRecord, run_id) is not None


@pytest.mark.anyio
async def test_never_deletes_running_or_waiting_approval_runs(db_session) -> None:
    if not await _workflow_tables_available(db_session):
        pytest.skip("workflow tables not available — run alembic upgrade head")

    owner_id = await _create_owner(db_session)
    definition_id = await _create_definition(db_session, owner_id=owner_id)
    stale_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=120)
    running_id = await _create_run(
        db_session,
        owner_id=owner_id,
        definition_id=definition_id,
        status=RunStatus.RUNNING,
        updated_at=stale_at,
    )
    waiting_id = await _create_run(
        db_session,
        owner_id=owner_id,
        definition_id=definition_id,
        status=RunStatus.WAITING_APPROVAL,
        updated_at=stale_at,
    )

    factory = make_queue_session_factory(db_session.bind)
    result = await workflow_run_retention_cleanup(
        _sample_job(),
        settings=_retention_settings(),
        session_factory=factory,
    )

    assert result.counts["workflow_runs_deleted"] == 0
    async with factory() as session:
        assert await session.get(WorkflowRunRecord, running_id) is not None
        assert await session.get(WorkflowRunRecord, waiting_id) is not None


@pytest.mark.anyio
async def test_batching_deletes_more_than_one_batch_in_single_invocation(
    db_session,
) -> None:
    if not await _workflow_tables_available(db_session):
        pytest.skip("workflow tables not available — run alembic upgrade head")

    owner_id = await _create_owner(db_session)
    definition_id = await _create_definition(db_session, owner_id=owner_id)
    stale_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=60)
    run_ids = [
        await _create_run(
            db_session,
            owner_id=owner_id,
            definition_id=definition_id,
            status=RunStatus.COMPLETED,
            updated_at=stale_at,
        )
        for _ in range(5)
    ]

    settings = _retention_settings()
    factory = make_queue_session_factory(db_session.bind)
    with patch.object(retention_module, "_DELETE_BATCH_SIZE", 2):
        result = await workflow_run_retention_cleanup(
            _sample_job(),
            settings=settings,
            session_factory=factory,
        )

    assert result.counts["workflow_runs_deleted"] == 5
    del run_ids
    async with factory() as session:
        remaining = await session.scalars(select(WorkflowRunRecord.id))
        assert list(remaining.all()) == []


@pytest.mark.anyio
async def test_purges_old_terminal_background_jobs_but_not_running_self(
    db_session,
) -> None:
    if not await background_jobs_table_available(db_session):
        pytest.skip("background_jobs not available — run alembic upgrade head")

    stale_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=45)
    old_job_ids = [
        await _insert_terminal_background_job(db_session, updated_at=stale_at)
        for _ in range(2)
    ]
    current_job = _sample_job()
    # Stale terminal row matching the purge filter; only exclude_job_id keeps it.
    await _insert_terminal_background_job(
        db_session,
        updated_at=stale_at,
        status="succeeded",
        job_id=current_job.id,
    )
    recent_job_id = await _insert_terminal_background_job(
        db_session,
        updated_at=datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=5),
    )

    factory = make_queue_session_factory(db_session.bind)
    result = await workflow_run_retention_cleanup(
        current_job,
        settings=_retention_settings(),
        session_factory=factory,
    )

    assert result.counts["background_jobs_deleted"] == 2
    async with factory() as session:
        rows = await session.execute(
            text("SELECT id::text FROM background_jobs ORDER BY id")
        )
        remaining_ids = {uuid.UUID(row[0]) for row in rows.all()}
    assert current_job.id in remaining_ids
    assert recent_job_id in remaining_ids
    assert not any(job_id in remaining_ids for job_id in old_job_ids)


@pytest.mark.anyio
async def test_deletes_stale_upload_staging_rows(db_session) -> None:
    if not await _upload_staging_table_available(db_session):
        pytest.skip("document_upload_staging not available — run alembic upgrade head")

    owner_id = await _create_owner(db_session)
    store = SqlDocumentStore(db_session)
    stale_document = await store.create_document(
        user_id=owner_id,
        filename="stale.txt",
        mime_type="text/plain",
        status="pending",
    )
    recent_document = await store.create_document(
        user_id=owner_id,
        filename="recent.txt",
        mime_type="text/plain",
        status="pending",
    )
    await store.store_upload_staging(
        document_id=stale_document.id,
        user_id=owner_id,
        file_bytes=b"stale",
        filename="stale.txt",
        mime_type="text/plain",
    )
    await store.store_upload_staging(
        document_id=recent_document.id,
        user_id=owner_id,
        file_bytes=b"recent",
        filename="recent.txt",
        mime_type="text/plain",
    )
    stale_at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=45)
    await db_session.execute(
        update(DocumentUploadStaging)
        .where(DocumentUploadStaging.document_id == stale_document.id)
        .values(created_at=stale_at)
    )
    await db_session.commit()

    factory = make_queue_session_factory(db_session.bind)
    result = await workflow_run_retention_cleanup(
        _sample_job(),
        settings=_retention_settings(),
        session_factory=factory,
    )

    assert result.counts["upload_staging_deleted"] == 1
    assert await store.fetch_upload_staging(stale_document.id) is None
    assert await store.fetch_upload_staging(recent_document.id) is not None
