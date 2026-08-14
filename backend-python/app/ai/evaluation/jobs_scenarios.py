"""Reference scenario runners for Background Jobs eval cases (Epic 10 Phase 9)."""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ai.agent.executor.agent_executor import AgentExecutor
from app.ai.agent.executor.result_aggregator import AggregatedToolResults
from app.ai.agent.models.response import AgentResponse
from app.ai.agent.models.state import AgentExecutionState, AgentExecutionStatus
from app.ai.deps import (
    build_agent_approval_service_for_session,
    build_hitl_resume_executor,
    build_workflow_manager_for_session,
)
from app.ai.documents.pipeline import IngestionPipeline
from app.ai.evaluation.datasets import EvalCase, JOBS_SCENARIO_JOB_TYPES
from app.ai.hitl.models import ApprovalStatus, ProposedToolCall
from app.ai.hitl.service import AgentApprovalService
from app.ai.hitl.store import AgentToolApprovalStore
from app.ai.jobs.handlers.hitl_expiry import hitl_approval_expiry_sweep
from app.ai.jobs.handlers.hitl_orphan_sweep import hitl_orphaned_snapshot_sweep
from app.ai.jobs.handlers.rag_indexing import rag_document_indexing
from app.ai.jobs.handlers.scheduled_eval import scheduled_evaluation_run
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
from app.db.models import AgentToolApprovalRecord, ChatMessage, ChatSession, User
from app.db.models import WorkflowRunRecord

_EMBED_DIMENSIONS = 1536


class _FakeEmbeddingProvider:
    dimensions = _EMBED_DIMENSIONS

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [
            [float(index % _EMBED_DIMENSIONS), 0.0] + [0.0] * (_EMBED_DIMENSIONS - 2)
            for index, _ in enumerate(texts)
        ]


@dataclass(frozen=True)
class JobsScenarioOutcome:
    passed: bool
    error: str | None = None


def jobs_scenario_job_type(scenario: str) -> str:
    try:
        return JOBS_SCENARIO_JOB_TYPES[scenario]  # type: ignore[index]
    except KeyError as exc:
        raise ValueError(f"unknown job_scenario {scenario!r}") from exc


def _sample_job(*, job_type: str) -> BackgroundJob:
    now = datetime.datetime.now(datetime.UTC)
    return BackgroundJob(
        id=uuid.uuid4(),
        job_type=job_type,
        status=JobStatus.RUNNING,
        payload={"version": 1},
        attempt_count=1,
        max_attempts=3,
        version=1,
        run_at=now,
        created_at=now,
        updated_at=now,
        locked_by="eval-worker",
        locked_at=now,
        started_at=now,
    )


async def _hitl_tables_available(session: AsyncSession) -> bool:
    result = await session.scalar(
        text("SELECT to_regclass('public.agent_tool_approvals') IS NOT NULL")
    )
    return bool(result)


async def _workflow_tables_available(session: AsyncSession) -> bool:
    result = await session.scalar(
        text("SELECT to_regclass('public.workflow_runs') IS NOT NULL")
    )
    return bool(result)


async def _pgvector_available(session: AsyncSession) -> bool:
    result = await session.scalar(
        text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
    )
    return result == 1


async def _staging_table_available(session: AsyncSession) -> bool:
    result = await session.scalar(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'document_upload_staging'"
        )
    )
    return result == 1


async def run_jobs_reference_scenario(
    case: EvalCase,
    *,
    session: AsyncSession,
    settings: Settings,
) -> JobsScenarioOutcome:
    scenario = case.job_scenario
    if scenario is None:
        return JobsScenarioOutcome(passed=False, error="job_scenario is required")

    expected_type = jobs_scenario_job_type(scenario)
    if case.job_type != expected_type:
        return JobsScenarioOutcome(
            passed=False,
            error=(
                f"job_type {case.job_type!r} does not match scenario "
                f"{scenario!r} ({expected_type})"
            ),
        )

    factory = async_sessionmaker(session.bind, expire_on_commit=False)
    eval_settings = settings.model_copy(update={"background_jobs_enabled": True})

    try:
        if scenario == "hitl_expiry_agent":
            return await _run_hitl_expiry_agent(factory, eval_settings)
        if scenario == "hitl_expiry_workflow":
            return await _run_hitl_expiry_workflow(session, factory, eval_settings)
        if scenario == "orphan_sweep_resume":
            return await _run_orphan_sweep_resume(session, factory, eval_settings)
        if scenario == "workflow_retention":
            return await _run_workflow_retention(session, factory, eval_settings)
        if scenario == "rag_indexing":
            return await _run_rag_indexing(session, factory, eval_settings)
        if scenario == "scheduled_eval":
            return await _run_scheduled_eval(eval_settings)
        return JobsScenarioOutcome(
            passed=False, error=f"unsupported scenario {scenario!r}"
        )
    except Exception as exc:
        await session.rollback()
        return JobsScenarioOutcome(passed=False, error=str(exc))


async def _run_hitl_expiry_agent(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> JobsScenarioOutcome:
    async with session_factory() as session:
        if not await _hitl_tables_available(session):
            return JobsScenarioOutcome(
                passed=False, error="agent_tool_approvals table unavailable"
            )

        user = User(
            auth_provider="google",
            external_auth_id=f"eval-expiry-{uuid.uuid4().hex}",
            email=f"eval-expiry-{uuid.uuid4().hex[:8]}@example.com",
        )
        session.add(user)
        await session.flush()
        chat_session = ChatSession(user_id=user.id, next_seq=1)
        session.add(chat_session)
        await session.flush()

        store = AgentToolApprovalStore(session)
        approval = await store.create(
            session_id=chat_session.id,
            owner_id=user.id,
            execution_id="eval-expiry-agent",
            approval_correlation_id=uuid.uuid4(),
            proposed_calls=[
                ProposedToolCall(name="echo", arguments={"message": "x"}, call_id="c1")
            ],
            paused_scratchpad=[{"kind": "thought", "content": "x"}],
            paused_state={
                "execution_id": "eval-expiry-agent",
                "status": "waiting_approval",
            },
        )
        await session.execute(
            update(AgentToolApprovalRecord)
            .where(AgentToolApprovalRecord.id == approval.id)
            .values(
                requested_at=datetime.datetime.now(datetime.UTC)
                - datetime.timedelta(hours=2)
            )
        )
        await session.commit()

    result = await hitl_approval_expiry_sweep(
        _sample_job(job_type="hitl_approval_expiry_sweep"),
        settings=settings.model_copy(
            update={
                "hitl_approval_timeout_hours": 1,
                "workflow_approval_timeout_hours": 0,
            }
        ),
        session_factory=session_factory,
        build_workflow_manager=lambda db_session: build_workflow_manager_for_session(
            db_session, settings
        ),
    )
    if result.counts.get("agent_expired", 0) != 1:
        return JobsScenarioOutcome(
            passed=False,
            error=f"expected agent_expired=1, got {result.counts}",
        )
    return JobsScenarioOutcome(passed=True)


async def _run_hitl_expiry_workflow(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> JobsScenarioOutcome:
    if not await _hitl_tables_available(session):
        return JobsScenarioOutcome(
            passed=False, error="agent_tool_approvals table unavailable"
        )
    if not await _workflow_tables_available(session):
        return JobsScenarioOutcome(
            passed=False, error="workflow_runs table unavailable"
        )

    owner = await SqlUserStore(session).create(
        sub=f"eval-wf-expiry-{uuid.uuid4()}",
        email=None,
        name=None,
        picture=None,
    )
    now = datetime.datetime.now(datetime.UTC)
    definition = WorkflowDefinition(
        id=uuid.uuid4(),
        owner_id=owner.id,
        name="eval-approval-expiry",
        status=DefinitionStatus.ACTIVE,
        entry_node_id="approve",
        nodes=[
            WorkflowNode(id="approve", type=NodeType.APPROVAL, config={}),
            WorkflowNode(id="end", type=NodeType.TERMINAL, config={}),
        ],
        edges=[WorkflowEdge(id="e1", from_node_id="approve", to_node_id="end")],
        created_at=now,
        updated_at=now,
    )
    workflow_store = PostgresWorkflowStore(session, settings)
    persisted = await workflow_store.create_definition(definition)
    run = await workflow_store.create_run(
        WorkflowRun(
            id=uuid.uuid4(),
            workflow_definition_id=persisted.id,
            owner_id=owner.id,
            idempotency_key=f"eval-{uuid.uuid4().hex}",
            status=RunStatus.WAITING_APPROVAL,
            context=WorkflowContext(trigger_input={}),
            current_node_ids=["approve"],
            checkpoint_version=1,
            created_at=now,
            updated_at=now,
            started_at=now,
        )
    )
    await workflow_store.append_node_execution(
        WorkflowNodeExecution(
            id=uuid.uuid4(),
            run_id=run.id,
            node_id="approve",
            node_type=NodeType.APPROVAL,
            attempt=1,
            status=NodeStatus.WAITING_APPROVAL,
            input={},
            started_at=now - datetime.timedelta(hours=3),
        )
    )
    await session.commit()

    result = await hitl_approval_expiry_sweep(
        _sample_job(job_type="hitl_approval_expiry_sweep"),
        settings=settings.model_copy(
            update={
                "hitl_approval_timeout_hours": 0,
                "workflow_approval_timeout_hours": 1,
            }
        ),
        session_factory=session_factory,
        build_workflow_manager=lambda db_session: build_workflow_manager_for_session(
            db_session, settings
        ),
    )
    if result.counts.get("workflow_expired", 0) != 1:
        return JobsScenarioOutcome(
            passed=False,
            error=f"expected workflow_expired=1, got {result.counts}",
        )
    return JobsScenarioOutcome(passed=True)


async def _run_orphan_sweep_resume(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> JobsScenarioOutcome:
    if not await _hitl_tables_available(session):
        return JobsScenarioOutcome(
            passed=False, error="agent_tool_approvals table unavailable"
        )

    user = User(
        auth_provider="google",
        external_auth_id=f"eval-orphan-{uuid.uuid4().hex}",
        email=f"eval-orphan-{uuid.uuid4().hex[:8]}@example.com",
    )
    session.add(user)
    await session.flush()
    chat_session = ChatSession(user_id=user.id, next_seq=2)
    session.add(chat_session)
    await session.flush()

    store = AgentToolApprovalStore(session)
    approval = await store.create(
        session_id=chat_session.id,
        owner_id=user.id,
        execution_id="eval-orphan",
        approval_correlation_id=uuid.uuid4(),
        proposed_calls=[
            ProposedToolCall(name="echo", arguments={"message": "x"}, call_id="c1")
        ],
        paused_scratchpad=[{"kind": "thought", "content": "resume me"}],
        paused_state=AgentExecutionState(
            execution_id="eval-orphan",
            status=AgentExecutionStatus.WAITING_APPROVAL,
            current_iteration=1,
        ).model_dump(mode="json"),
    )
    await store.cas_decide(
        approval.id,
        owner_id=user.id,
        status=ApprovalStatus.APPROVED,
        decided_by=user.id,
    )
    placeholder = ChatMessage(
        session_id=chat_session.id,
        seq=1,
        role="assistant",
        content="",
        status="waiting_approval",
        pending_approval_id=approval.id,
    )
    session.add(placeholder)
    await session.flush()
    await store.link_pending_message(approval.id, pending_message_id=placeholder.id)
    await session.execute(
        update(AgentToolApprovalRecord)
        .where(AgentToolApprovalRecord.id == approval.id)
        .values(
            decided_at=datetime.datetime.now(datetime.UTC)
            - datetime.timedelta(minutes=10)
        )
    )
    await session.commit()

    eval_settings = settings.model_copy(
        update={
            "hitl_enabled": True,
            "hitl_orphan_sweep_grace_seconds": 60,
        }
    )

    async def _stub_execute_approved_calls(
        _calls: list[ProposedToolCall],
        *,
        execution_id: str,
        tool_context: object,
        scratchpad: object,
        stream_publisher: object,
    ) -> AggregatedToolResults:
        del _calls, execution_id, tool_context, scratchpad, stream_publisher
        return AggregatedToolResults(records=[])

    async def _stub_resume(*_args: object, **_kwargs: object) -> AgentResponse:
        return AgentResponse(content="Recovered.", finish_reason="stop")

    def _build_eval_approval_service(db_session: AsyncSession) -> AgentApprovalService:
        service = build_agent_approval_service_for_session(db_session, eval_settings)
        service._execute_approved_calls = _stub_execute_approved_calls  # type: ignore[method-assign]
        return service

    def _build_eval_resume_executor(
        db_session: AsyncSession,
        service: AgentApprovalService,
    ) -> AgentExecutor:
        executor = build_hitl_resume_executor(
            eval_settings,
            approval_service=service,
            session=db_session,
        )
        executor.resume_from_approval = _stub_resume  # type: ignore[method-assign]
        return executor

    result = await hitl_orphaned_snapshot_sweep(
        _sample_job(job_type="hitl_orphaned_snapshot_sweep"),
        settings=eval_settings,
        session_factory=session_factory,
        build_approval_service=_build_eval_approval_service,
        build_resume_executor=_build_eval_resume_executor,
    )
    if result.counts.get("resumed", 0) < 1:
        return JobsScenarioOutcome(
            passed=False,
            error=f"expected resumed>=1, got {result.counts}",
        )

    async with session_factory() as verify_session:
        refreshed = await AgentToolApprovalStore(verify_session).get(approval.id)
    if (
        refreshed is None
        or refreshed.paused_scratchpad != []
        or refreshed.paused_state != {}
    ):
        return JobsScenarioOutcome(
            passed=False,
            error="expected eval orphan approval to be resumed and cleared",
        )
    return JobsScenarioOutcome(passed=True)


async def _run_workflow_retention(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> JobsScenarioOutcome:
    if not await _workflow_tables_available(session):
        return JobsScenarioOutcome(
            passed=False, error="workflow_runs table unavailable"
        )

    owner = await SqlUserStore(session).create(
        sub=f"eval-retention-{uuid.uuid4()}",
        email=None,
        name=None,
        picture=None,
    )
    now = datetime.datetime.now(datetime.UTC)
    definition = WorkflowDefinition(
        id=uuid.uuid4(),
        owner_id=owner.id,
        name="eval-retention",
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
    workflow_store = PostgresWorkflowStore(session, settings)
    persisted = await workflow_store.create_definition(definition)
    run_id = uuid.uuid4()
    await workflow_store.create_run(
        WorkflowRun(
            id=run_id,
            workflow_definition_id=persisted.id,
            owner_id=owner.id,
            idempotency_key=f"eval-retention-{uuid.uuid4().hex}",
            status=RunStatus.COMPLETED,
            context=WorkflowContext(trigger_input={}),
            current_node_ids=["end"],
            checkpoint_version=1,
            created_at=now,
            updated_at=now,
            started_at=now,
            completed_at=now,
        )
    )
    await session.execute(
        update(WorkflowRunRecord)
        .where(WorkflowRunRecord.id == run_id)
        .values(updated_at=now - datetime.timedelta(days=60))
    )
    await session.commit()

    result = await workflow_run_retention_cleanup(
        _sample_job(job_type="workflow_run_retention_cleanup"),
        settings=settings.model_copy(update={"workflow_run_retention_days": 30}),
        session_factory=session_factory,
    )
    deleted = result.counts.get("workflow_runs_deleted", 0)
    if deleted < 1:
        return JobsScenarioOutcome(
            passed=False,
            error=f"expected workflow_runs_deleted>=1, got {result.counts}",
        )
    return JobsScenarioOutcome(passed=True)


async def _run_rag_indexing(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> JobsScenarioOutcome:
    if not await _pgvector_available(session):
        return JobsScenarioOutcome(passed=False, error="pgvector extension unavailable")
    if not await _staging_table_available(session):
        return JobsScenarioOutcome(
            passed=False, error="document_upload_staging table unavailable"
        )

    user = await SqlUserStore(session).create(
        sub=f"eval-rag-{uuid.uuid4()}",
        email=None,
        name=None,
        picture=None,
    )
    user_id = user.id
    doc_store = SqlDocumentStore(session)
    document = await doc_store.create_document(
        user_id=user_id,
        filename="eval-sample.txt",
        mime_type="text/plain",
    )
    await doc_store.store_upload_staging(
        document_id=document.id,
        user_id=user_id,
        file_bytes=b"hello eval\n",
        filename="eval-sample.txt",
        mime_type="text/plain",
    )
    await session.commit()

    def build_pipeline(_settings: Settings) -> IngestionPipeline:
        pipeline = IngestionPipeline(
            _settings, embedding_provider=_FakeEmbeddingProvider()
        )
        return pipeline

    job = BackgroundJob(
        id=uuid.uuid4(),
        job_type="rag_document_indexing",
        status=JobStatus.RUNNING,
        payload={
            "version": 1,
            "document_id": str(document.id),
            "user_id": str(user_id),
        },
        attempt_count=1,
        max_attempts=3,
        version=1,
        run_at=datetime.datetime.now(datetime.UTC),
        created_at=datetime.datetime.now(datetime.UTC),
        updated_at=datetime.datetime.now(datetime.UTC),
    )
    result = await rag_document_indexing(
        job,
        settings=settings,
        session_factory=session_factory,
        build_pipeline=build_pipeline,
    )
    async with session_factory() as verify_session:
        refreshed = await SqlDocumentStore(verify_session).get_owned_document(
            document.id,
            user_id=user_id,
        )
    if refreshed is None or refreshed.status != "ready":
        return JobsScenarioOutcome(
            passed=False,
            error=f"document not indexed: status={getattr(refreshed, 'status', None)}",
        )
    if result.summary is None:
        return JobsScenarioOutcome(passed=False, error="handler returned no summary")
    return JobsScenarioOutcome(passed=True)


async def _run_scheduled_eval(settings: Settings) -> JobsScenarioOutcome:
    with TemporaryDirectory() as tmp_dir:
        job = _sample_job(job_type="scheduled_evaluation_run")
        eval_settings = settings.model_copy(
            update={
                "evaluation_schedule_level": "prompt",
                "evaluation_schedule_enabled": False,
            }
        )
        result = await scheduled_evaluation_run(
            job,
            settings=eval_settings,
            output_dir=Path(tmp_dir),
        )
    if result.summary is None:
        return JobsScenarioOutcome(
            passed=False, error="scheduled eval returned no summary"
        )
    return JobsScenarioOutcome(passed=True)
