"""Epic 11 Phase 3: audit events emitted from tool authorization and RBAC changes."""

from __future__ import annotations

import uuid

import pytest

from app.ai.security.audit.logger import AuditLogger
from app.ai.security.audit.models import AuditEvent, AuditOutcome
from app.ai.security.rbac.models import Role, UserRoleAssignment
from app.ai.security.rbac.service import RbacService
from app.ai.tools.executor import ToolExecutor
from app.ai.tools.registry import ToolRegistry
from app.ai.tools.schemas import (
    ToolCall,
    ToolDefinition,
    ToolExecutionContext,
    ToolResult,
)
from app.core.caller import CallerContext
from app.core.config import Settings


class _Handler:
    async def execute(
        self, args: dict[str, object], context: ToolExecutionContext
    ) -> ToolResult:
        del args, context
        return ToolResult(success=True, data={})


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

    async def count(self, **_: object) -> int:
        return len(self.events)


class FakeRoleStore:
    def __init__(self) -> None:
        self.roles = {
            "admin": Role(id=uuid.uuid4(), name="admin", description="admin"),
        }
        self._user_roles: dict[uuid.UUID, set[str]] = {}

    async def list_roles(self) -> list[Role]:
        return list(self.roles.values())

    async def get_role_by_name(self, name: str) -> Role | None:
        return self.roles.get(name.lower())

    async def get_permission_keys_for_user(self, user_id: uuid.UUID) -> set[str]:
        return set()

    async def get_user_roles(self, user_id: uuid.UUID) -> list[str]:
        return sorted(self._user_roles.get(user_id, set()))

    async def assign_role(self, user_id: uuid.UUID, role_name: str) -> bool:
        target = role_name.lower()
        roles = self._user_roles.setdefault(user_id, set())
        already = target in roles
        roles.add(target)
        return not already

    async def revoke_role(self, user_id: uuid.UUID, role_name: str) -> bool:
        roles = self._user_roles.get(user_id, set())
        if role_name.lower() not in roles:
            return False
        roles.remove(role_name.lower())
        return True

    async def bootstrap_admins(self, emails: list[str]) -> int:
        return 0

    async def get_user_role_assignments(
        self, user_id: uuid.UUID
    ) -> list[UserRoleAssignment]:
        return []


def _settings() -> Settings:
    return Settings(
        openai_api_key="test-key",
        security_governance_enabled=True,
        security_audit_log_enabled=True,
    )


@pytest.mark.anyio
async def test_guest_tool_denial_emits_tool_execution_denied_event() -> None:
    registry = ToolRegistry()
    tool = ToolDefinition(
        name="echo", description="echo", parameters={"type": "object", "properties": {}}
    )
    registry.register(tool, _Handler())
    audit_store = FakeAuditStore()
    settings = _settings()
    executor = ToolExecutor(
        registry=registry,
        settings=settings,
        audit_logger=AuditLogger(audit_store, settings=settings),
    )
    context = ToolExecutionContext(caller=CallerContext.anonymous())

    result = await executor.execute(
        ToolCall(name="echo", arguments={}, call_id="c1"),
        context,
    )

    assert result.success is False
    assert result.error_code == "forbidden"
    assert len(audit_store.events) == 1
    event = audit_store.events[0]
    assert event.action == "tool.execution.denied"
    assert event.outcome is AuditOutcome.DENIED
    assert event.resource_type == "tool"
    assert event.resource_id == "echo"
    # Only the denial reason string is recorded — never raw tool arguments.
    assert "secret" not in str(event.metadata)
    assert "should-not-leak" not in str(event.metadata)


@pytest.mark.anyio
async def test_assign_and_revoke_role_emit_audit_events() -> None:
    audit_store = FakeAuditStore()
    settings = _settings()
    service = RbacService(
        FakeRoleStore(),
        audit_logger=AuditLogger(audit_store, settings=settings),
    )
    user_id = uuid.uuid4()
    actor = CallerContext.for_user(uuid.uuid4())

    assigned = await service.assign_role(user_id, "admin", actor=actor)
    revoked = await service.revoke_role(user_id, "admin", actor=actor)

    assert assigned is True
    assert revoked is True
    assert [event.action for event in audit_store.events] == [
        "role.assigned",
        "role.revoked",
    ]
    for event in audit_store.events:
        assert event.outcome is AuditOutcome.SUCCESS
        assert event.resource_type == "role"
        assert event.resource_id == str(user_id)
        assert event.metadata == {"role": "admin"}
        assert event.actor_user_id == actor.user_id


@pytest.mark.anyio
async def test_no_audit_event_when_role_assignment_is_a_noop() -> None:
    audit_store = FakeAuditStore()
    settings = _settings()
    service = RbacService(
        FakeRoleStore(),
        audit_logger=AuditLogger(audit_store, settings=settings),
    )
    user_id = uuid.uuid4()

    await service.assign_role(user_id, "admin")
    already_assigned = await service.assign_role(user_id, "admin")

    assert already_assigned is False
    assert len(audit_store.events) == 1
