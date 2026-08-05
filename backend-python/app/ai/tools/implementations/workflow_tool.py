"""Agent-invocable workflow execution tool (Epic 06 Phase 10)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from app.ai.tools.schemas import ToolDefinition, ToolExecutionContext, ToolResult
from app.ai.workflow.exceptions import (
    WorkflowNotFoundError,
    WorkflowValidationError,
)
from app.core.config import Settings
from app.core.logging import get_logger
from app.schemas.workflow import to_run_detail_response, to_run_response

if TYPE_CHECKING:
    from app.ai.workflow.manager import WorkflowManager

_logger = get_logger(__name__)

WORKFLOW_EXECUTION_TOOL_NAME = "workflow_execution"


WORKFLOW_EXECUTION_TOOL_DEFINITION = ToolDefinition(
    name=WORKFLOW_EXECUTION_TOOL_NAME,
    description=(
        "Start a workflow run or check the status of an existing run. "
        "Use action=start with definition_id and idempotency_key to launch a "
        "workflow asynchronously; use action=status with run_id to poll progress."
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["start", "status"],
                "description": "Whether to start a new run or check an existing run.",
            },
            "definition_id": {
                "type": "string",
                "description": "Workflow definition UUID (required for action=start).",
            },
            "idempotency_key": {
                "type": "string",
                "description": (
                    "Caller-supplied dedupe key (required for action=start). "
                    "Retries with the same key return the existing run."
                ),
            },
            "input": {
                "type": "object",
                "description": "Optional trigger input payload for action=start.",
            },
            "run_id": {
                "type": "string",
                "description": "Workflow run UUID (required for action=status).",
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    },
)

ManagerFactory = Callable[[], Awaitable["WorkflowManager"]]


class WorkflowExecutionToolHandler:
    """Execute workflow start/status actions through ``WorkflowManager``."""

    def __init__(
        self,
        *,
        settings: Settings,
        manager_factory: ManagerFactory | None = None,
    ) -> None:
        self._settings = settings
        self._manager_factory = manager_factory

    async def execute(
        self,
        args: dict[str, object],
        context: ToolExecutionContext,
    ) -> ToolResult:
        action = args.get("action")
        if not isinstance(action, str) or action not in {"start", "status"}:
            return ToolResult(
                success=False,
                error="action must be 'start' or 'status'",
                error_code="validation_error",
            )

        owner_id = context.caller.user_id
        if owner_id is None:
            return ToolResult(
                success=False,
                error="Tool invocation requires an authenticated user",
                error_code="forbidden",
            )

        if action == "start":
            return await self._execute_start(args, context, owner_id=owner_id)
        return await self._execute_status(args, owner_id=owner_id)

    async def _execute_start(
        self,
        args: dict[str, object],
        context: ToolExecutionContext,
        *,
        owner_id: uuid.UUID,
    ) -> ToolResult:
        definition_id = _parse_uuid(args.get("definition_id"), "definition_id")
        if isinstance(definition_id, ToolResult):
            return definition_id

        idempotency_key = args.get("idempotency_key")
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            return ToolResult(
                success=False,
                error="idempotency_key must be a non-empty string",
                error_code="validation_error",
            )

        trigger_input = args.get("input", {})
        if trigger_input is None:
            trigger_input = {}
        if not isinstance(trigger_input, dict):
            return ToolResult(
                success=False,
                error="input must be an object",
                error_code="validation_error",
            )

        try:
            async with self._manager_scope() as manager:
                run = await manager.start_run(
                    definition_id,
                    owner_id=owner_id,
                    idempotency_key=idempotency_key.strip(),
                    trigger_input=trigger_input,
                    session_id=context.session_id,
                )
        except WorkflowNotFoundError as exc:
            return ToolResult(
                success=False,
                error=str(exc),
                error_code="workflow_not_found",
            )
        except WorkflowValidationError as exc:
            return ToolResult(
                success=False,
                error=str(exc),
                error_code="workflow_validation_error",
            )
        except Exception:
            _logger.warning(
                "Workflow tool start failed",
                owner_id=str(owner_id),
                definition_id=str(definition_id),
                exc_info=True,
            )
            return ToolResult(
                success=False,
                error="Workflow execution is temporarily unavailable",
                error_code="handler_error",
            )

        return ToolResult(
            success=True,
            data=to_run_response(run).model_dump(mode="json"),
        )

    async def _execute_status(
        self,
        args: dict[str, object],
        *,
        owner_id: uuid.UUID,
    ) -> ToolResult:
        run_id = _parse_uuid(args.get("run_id"), "run_id")
        if isinstance(run_id, ToolResult):
            return run_id

        try:
            async with self._manager_scope() as manager:
                with_executions = await manager.get_run_with_executions(
                    run_id,
                    owner_id=owner_id,
                )
        except Exception:
            _logger.warning(
                "Workflow tool status failed",
                owner_id=str(owner_id),
                run_id=str(run_id),
                exc_info=True,
            )
            return ToolResult(
                success=False,
                error="Workflow execution is temporarily unavailable",
                error_code="handler_error",
            )

        if with_executions is None:
            return ToolResult(
                success=False,
                error=f"Workflow run {run_id} not found.",
                error_code="workflow_not_found",
            )

        run, executions = with_executions
        return ToolResult(
            success=True,
            data=to_run_detail_response(run, executions).model_dump(mode="json"),
        )

    @asynccontextmanager
    async def _manager_scope(self) -> AsyncIterator[WorkflowManager]:
        if self._manager_factory is not None:
            yield await self._manager_factory()
            return

        from app.db.engine import get_sessionmaker

        sessionmaker = get_sessionmaker()
        async with sessionmaker() as session:
            from app.ai.deps import build_workflow_manager_for_session

            manager = build_workflow_manager_for_session(session, self._settings)
            try:
                yield manager
                await session.commit()
            except Exception:
                await session.rollback()
                raise


def _parse_uuid(value: object, field_name: str) -> uuid.UUID | ToolResult:
    if not isinstance(value, str) or not value.strip():
        return ToolResult(
            success=False,
            error=f"{field_name} must be a non-empty string",
            error_code="validation_error",
        )
    try:
        return uuid.UUID(value.strip())
    except ValueError:
        return ToolResult(
            success=False,
            error=f"{field_name} must be a valid UUID",
            error_code="validation_error",
        )


def create_workflow_execution_handler(
    settings: Settings,
    *,
    manager_factory: ManagerFactory | None = None,
) -> WorkflowExecutionToolHandler:
    """Build the workflow execution tool handler for registration or tests."""
    return WorkflowExecutionToolHandler(
        settings=settings,
        manager_factory=manager_factory,
    )
