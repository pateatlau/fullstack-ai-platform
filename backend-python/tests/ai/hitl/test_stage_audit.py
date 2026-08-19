"""Epic 11 Phase 3: audit events emitted from HITL stage/terminal decisions."""

from __future__ import annotations

import uuid

import pytest

from app.ai.hitl.exceptions import StagePermissionInvalidError
from app.ai.hitl.models import ApprovalStatus, ProposedToolCall
from app.ai.hitl.service import AgentApprovalService
from app.ai.security.audit.logger import AuditLogger
from app.ai.security.audit.models import AuditEvent, AuditOutcome
from app.ai.security.rbac.models import Role, UserRoleAssignment
from app.ai.security.rbac.permissions import PermissionKey
from app.ai.security.rbac.service import RbacService
from app.ai.tools.executor import ToolExecutor
from app.ai.tools.registry import ToolRegistry
from app.ai.tools.schemas import ToolDefinition, ToolExecutionContext, ToolResult
from app.core.config import Settings
from tests.ai.hitl.fakes import InMemoryApprovalStore
from tests.fakes import FakeChatStore


class _Handler:
    async def execute(
        self, args: dict[str, object], context: ToolExecutionContext
    ) -> ToolResult:
        del args, context
        return ToolResult(success=True, data={})


class _FakeRoleStore:
    def __init__(self, *, user_roles: dict[uuid.UUID, set[str]]) -> None:
        self._user_roles = user_roles

    async def list_roles(self) -> list[Role]:
        return []

    async def get_role_by_name(self, name: str) -> Role | None:
        return None

    async def get_permission_keys_for_user(self, user_id: uuid.UUID) -> set[str]:
        return set()

    async def get_user_roles(self, user_id: uuid.UUID) -> list[str]:
        return sorted(self._user_roles.get(user_id, set()))

    async def assign_role(self, user_id: uuid.UUID, role_name: str) -> bool:
        raise NotImplementedError

    async def revoke_role(self, user_id: uuid.UUID, role_name: str) -> bool:
        raise NotImplementedError

    async def bootstrap_admins(self, emails: list[str]) -> int:
        raise NotImplementedError

    async def get_user_role_assignments(
        self, user_id: uuid.UUID
    ) -> list[UserRoleAssignment]:
        raise NotImplementedError


class FakeAuditStore:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    async def insert(self, event: AuditEvent) -> None:
        self.events.append(event)

    async def get_by_id(self, event_id: uuid.UUID) -> AuditEvent | None:
        for event in self.events:
            if event.id == event_id:
                return event
        return None

    async def query(self, **_: object) -> list[AuditEvent]:
        return list(self.events)


def _settings() -> Settings:
    return Settings(
        openai_api_key="test-key",
        security_governance_enabled=True,
        security_audit_log_enabled=True,
    )


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="delete_file",
            description="delete",
            parameters={"type": "object", "properties": {}},
            requires_approval=True,
        ),
        _Handler(),
    )
    return registry


def _service(
    store: InMemoryApprovalStore,
    chat_store: FakeChatStore,
    *,
    user_roles: dict[uuid.UUID, set[str]],
    audit_store: FakeAuditStore,
) -> AgentApprovalService:
    registry = _registry()
    settings = _settings()
    rbac_service = RbacService(_FakeRoleStore(user_roles=user_roles))
    audit_logger = AuditLogger(audit_store, settings=settings)
    return AgentApprovalService(
        approval_store=store,
        chat_store=chat_store,
        tool_registry=registry,
        tool_executor=ToolExecutor(registry=registry, settings=settings),
        rbac_service=rbac_service,
        rbac_enforcement_enabled=True,
        audit_logger=audit_logger,
    )


_STAGE = PermissionKey.JOBS_RETRY.value


async def _seed_pending(
    *,
    store: InMemoryApprovalStore,
    chat_store: FakeChatStore,
    owner_id: uuid.UUID,
) -> uuid.UUID:
    session = await chat_store.create_session(user_id=owner_id)
    approval = await store.create(
        session_id=session.id,
        owner_id=owner_id,
        execution_id="exec-stage-audit",
        approval_correlation_id=uuid.uuid4(),
        proposed_calls=[
            ProposedToolCall(name="delete_file", arguments={}, call_id="c1")
        ],
        paused_scratchpad=[],
        paused_state={"execution_id": "exec-stage-audit", "status": "waiting_approval"},
        required_stages=[_STAGE],
    )
    return approval.id


@pytest.mark.anyio
async def test_stage_denial_emits_approval_stage_denied_event() -> None:
    owner_id = uuid.uuid4()
    store = InMemoryApprovalStore()
    chat_store = FakeChatStore()
    approval_id = await _seed_pending(
        store=store, chat_store=chat_store, owner_id=owner_id
    )
    audit_store = FakeAuditStore()
    service = _service(store, chat_store, user_roles={}, audit_store=audit_store)

    with pytest.raises(StagePermissionInvalidError):
        await service.decide(approval_id, decider_id=owner_id, decision="approved")

    assert len(audit_store.events) == 1
    event = audit_store.events[0]
    assert event.action == "approval.stage.denied"
    assert event.outcome is AuditOutcome.DENIED
    assert event.resource_type == "approval"
    assert event.resource_id == str(approval_id)
    # No raw tool argument/payload leakage — only the stage name.
    assert set(event.metadata.keys()) == {"stage"}


@pytest.mark.anyio
async def test_successful_decision_emits_stage_completed_and_decided_events() -> None:
    owner_id = uuid.uuid4()
    reviewer_id = uuid.uuid4()
    store = InMemoryApprovalStore()
    chat_store = FakeChatStore()
    approval_id = await _seed_pending(
        store=store, chat_store=chat_store, owner_id=owner_id
    )
    audit_store = FakeAuditStore()
    service = _service(
        store,
        chat_store,
        user_roles={reviewer_id: {"operator"}},
        audit_store=audit_store,
    )

    result = await service.decide(
        approval_id, decider_id=reviewer_id, decision="approved"
    )

    assert result.status is ApprovalStatus.APPROVED
    actions = [event.action for event in audit_store.events]
    assert actions == ["approval.stage.completed", "approval.decided"]
    for event in audit_store.events:
        assert event.outcome is AuditOutcome.SUCCESS
        assert event.resource_type == "approval"
        assert event.resource_id == str(approval_id)
        assert event.actor_user_id == reviewer_id
