from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.deps import get_audit_logger, get_rbac_service
from app.ai.security.audit.logger import AuditLogger
from app.ai.security.audit.models import AuditOutcome
from app.ai.security.errors import SecurityErrorCode
from app.ai.security.rbac.permissions import DEFAULT_ROLE_PERMISSIONS, PermissionKey
from app.ai.security.rbac.service import RbacService
from app.core.caller import CallerContext, require_authenticated_caller
from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.db.models import User
from app.db.session import get_db_session
from app.schemas.security import (
    SecurityAuditEntryResponse,
    SecurityAuditListResponse,
    SecurityPolicySummaryResponse,
    SecurityRoleAssignmentRequest,
    SecurityRoleResponse,
    SecurityUserRoleResponse,
)

router = APIRouter()


def _require_security_enabled(settings: Settings) -> None:
    if not settings.security_governance_enabled:
        raise AppError(
            code="feature_disabled",
            message="Security & Governance are not enabled on this server.",
            status_code=503,
        )


async def _require_permission(
    *,
    settings: Settings,
    rbac_service: RbacService,
    caller: CallerContext,
    permission: PermissionKey,
) -> None:
    if not settings.security_governance_enabled:
        raise AppError(
            code="feature_disabled",
            message="Security & Governance are not enabled on this server.",
            status_code=503,
        )
    if not settings.security_rbac_enforcement_enabled:
        return
    if caller.user_id is None:
        raise AppError(
            code=SecurityErrorCode.PERMISSION_DENIED.value,
            message=f"Requires the '{permission.value}' permission.",
            status_code=403,
        )
    decision = await rbac_service.authorize(caller.user_id, permission)
    if not decision.allowed:
        raise AppError(
            code=SecurityErrorCode.PERMISSION_DENIED.value,
            message=f"Requires the '{permission.value}' permission.",
            status_code=403,
        )


async def _require_manage_or_self(
    *,
    settings: Settings,
    rbac_service: RbacService,
    caller: CallerContext,
    user_id: uuid.UUID,
) -> None:
    if caller.user_id == user_id:
        return
    await _require_permission(
        settings=settings,
        rbac_service=rbac_service,
        caller=caller,
        permission=PermissionKey.RBAC_MANAGE,
    )


async def _ensure_user_exists(session: AsyncSession, user_id: uuid.UUID) -> None:
    exists = await session.scalar(select(User.id).where(User.id == user_id))
    if exists is None:
        raise AppError(
            code="user_not_found",
            message=f"User '{user_id}' was not found.",
            status_code=404,
        )


@router.get("/api/security/roles", response_model=list[SecurityRoleResponse])
async def list_security_roles(
    caller: CallerContext = Depends(require_authenticated_caller),
    settings: Settings = Depends(get_settings),
    rbac_service: RbacService = Depends(get_rbac_service),
) -> list[SecurityRoleResponse]:
    _require_security_enabled(settings)
    await _require_permission(
        settings=settings,
        rbac_service=rbac_service,
        caller=caller,
        permission=PermissionKey.RBAC_MANAGE,
    )

    responses: list[SecurityRoleResponse] = []
    for role_name in ("member", "operator", "admin", "owner"):
        permissions = sorted(
            str(permission_key)
            for permission_key in DEFAULT_ROLE_PERMISSIONS.get(role_name, set())
        )
        response = SecurityRoleResponse(
            name=role_name,
            description={
                "member": "Authenticated user baseline",
                "operator": "Operational user",
                "admin": "Administrative user",
                "owner": "Full platform owner",
            }[role_name],
            is_system=True,
            permissions=permissions,
        )
        responses.append(response)
    return responses


@router.get(
    "/api/security/users/{user_id}/roles", response_model=list[SecurityUserRoleResponse]
)
async def list_user_roles(
    user_id: uuid.UUID,
    caller: CallerContext = Depends(require_authenticated_caller),
    settings: Settings = Depends(get_settings),
    rbac_service: RbacService = Depends(get_rbac_service),
) -> list[SecurityUserRoleResponse]:
    _require_security_enabled(settings)
    await _require_manage_or_self(
        settings=settings,
        rbac_service=rbac_service,
        caller=caller,
        user_id=user_id,
    )

    explicit_roles = await rbac_service.get_user_roles(user_id)
    assignments = [
        SecurityUserRoleResponse(
            user_id=user_id,
            role_name=role_name,
            implicit=False,
        )
        for role_name in explicit_roles
    ]
    if not any(item.role_name == "member" for item in assignments):
        assignments.insert(
            0,
            SecurityUserRoleResponse(
                user_id=user_id,
                role_name="member",
                implicit=True,
            ),
        )
    return assignments


@router.post(
    "/api/security/users/{user_id}/roles", response_model=SecurityUserRoleResponse
)
async def assign_user_role(
    user_id: uuid.UUID,
    payload: SecurityRoleAssignmentRequest,
    caller: CallerContext = Depends(require_authenticated_caller),
    settings: Settings = Depends(get_settings),
    rbac_service: RbacService = Depends(get_rbac_service),
    session: AsyncSession = Depends(get_db_session),
) -> SecurityUserRoleResponse:
    _require_security_enabled(settings)
    await _require_permission(
        settings=settings,
        rbac_service=rbac_service,
        caller=caller,
        permission=PermissionKey.RBAC_MANAGE,
    )
    await _ensure_user_exists(session, user_id)

    role_name = payload.role_name.strip().lower()
    assigned = await rbac_service.assign_role(user_id, role_name, actor=caller)
    if not assigned:
        raise AppError(
            code=SecurityErrorCode.ROLE_NOT_FOUND.value,
            message=f"Role '{role_name}' was not found.",
            status_code=404,
        )
    return SecurityUserRoleResponse(
        user_id=user_id, role_name=role_name, implicit=False
    )


@router.delete("/api/security/users/{user_id}/roles/{role_name}")
async def revoke_user_role(
    user_id: uuid.UUID,
    role_name: str,
    caller: CallerContext = Depends(require_authenticated_caller),
    settings: Settings = Depends(get_settings),
    rbac_service: RbacService = Depends(get_rbac_service),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, str | uuid.UUID]:
    _require_security_enabled(settings)
    await _require_permission(
        settings=settings,
        rbac_service=rbac_service,
        caller=caller,
        permission=PermissionKey.RBAC_MANAGE,
    )
    await _ensure_user_exists(session, user_id)
    normalized = role_name.strip().lower()
    if normalized == "member":
        raise AppError(
            code=SecurityErrorCode.ROLE_ASSIGNMENT_INVALID.value,
            message="The 'member' role cannot be revoked.",
            status_code=400,
        )
    revoked = await rbac_service.revoke_role(user_id, normalized, actor=caller)
    if not revoked:
        raise AppError(
            code=SecurityErrorCode.ROLE_NOT_FOUND.value,
            message=f"Role assignment '{normalized}' was not found for user '{user_id}'.",
            status_code=404,
        )
    return {"user_id": user_id, "role_name": normalized}


@router.get("/api/security/audit", response_model=SecurityAuditListResponse)
async def list_security_audit(
    caller: CallerContext = Depends(require_authenticated_caller),
    settings: Settings = Depends(get_settings),
    audit_logger: AuditLogger = Depends(get_audit_logger),
    rbac_service: RbacService = Depends(get_rbac_service),
    actor_user_id: uuid.UUID | None = Query(default=None),
    action: str | None = Query(default=None),
    resource_type: str | None = Query(default=None),
    outcome: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> SecurityAuditListResponse:
    _require_security_enabled(settings)
    await _require_permission(
        settings=settings,
        rbac_service=rbac_service,
        caller=caller,
        permission=PermissionKey.AUDIT_VIEW,
    )

    parsed_outcome = None
    if outcome is not None:
        try:
            parsed_outcome = AuditOutcome(outcome)
        except ValueError as exc:
            raise AppError(
                code="validation_error",
                message=f"Unknown audit outcome '{outcome}'.",
                status_code=422,
            ) from exc

    rows = await audit_logger.query(
        actor_user_id=actor_user_id,
        action=action,
        resource_type=resource_type,
        outcome=parsed_outcome,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    return SecurityAuditListResponse(
        items=[SecurityAuditEntryResponse(**event.model_dump()) for event in rows],
        total=len(rows),
        limit=limit,
        offset=offset,
    )


@router.get("/api/security/audit/{audit_id}", response_model=SecurityAuditEntryResponse)
async def get_security_audit_entry(
    audit_id: uuid.UUID,
    caller: CallerContext = Depends(require_authenticated_caller),
    settings: Settings = Depends(get_settings),
    audit_logger: AuditLogger = Depends(get_audit_logger),
    rbac_service: RbacService = Depends(get_rbac_service),
) -> SecurityAuditEntryResponse:
    _require_security_enabled(settings)
    await _require_permission(
        settings=settings,
        rbac_service=rbac_service,
        caller=caller,
        permission=PermissionKey.AUDIT_VIEW,
    )
    event = await audit_logger.get_by_id(audit_id)
    if event is None:
        raise AppError(
            code="audit_not_found",
            message=f"Audit event '{audit_id}' was not found.",
            status_code=404,
        )
    return SecurityAuditEntryResponse(**event.model_dump())


@router.get("/api/security/policies", response_model=SecurityPolicySummaryResponse)
async def policy_summary(
    caller: CallerContext = Depends(require_authenticated_caller),
    settings: Settings = Depends(get_settings),
    rbac_service: RbacService = Depends(get_rbac_service),
) -> SecurityPolicySummaryResponse:
    _require_security_enabled(settings)
    await _require_permission(
        settings=settings,
        rbac_service=rbac_service,
        caller=caller,
        permission=PermissionKey.POLICY_VIEW,
    )

    roles = await rbac_service.list_roles()
    perm_registry = await rbac_service.get_permission_registry()
    return SecurityPolicySummaryResponse(
        security_governance_enabled=settings.security_governance_enabled,
        rbac_enforcement_enabled=settings.security_rbac_enforcement_enabled,
        guardrails_enabled=settings.security_guardrails_enabled,
        role_count=len(roles),
        permission_count=len(perm_registry),
        guardrail_rule_count=len(settings.security_guardrail_rules),
        audit_retention_days=settings.security_audit_retention_days,
        security_guardrails_mode=settings.security_guardrails_mode,
        feature_flags={
            "security_governance_enabled": settings.security_governance_enabled,
            "security_rbac_enforcement_enabled": settings.security_rbac_enforcement_enabled,
            "security_audit_log_enabled": settings.security_audit_log_enabled,
            "security_guardrails_enabled": settings.security_guardrails_enabled,
        },
    )
