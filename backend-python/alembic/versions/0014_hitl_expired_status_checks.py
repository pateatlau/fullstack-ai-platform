"""0014 hitl expired status checks

Revision ID: 0014_hitl_expired_status_checks
Revises: 0013_background_job_schedules
Create Date: 2026-08-12

Epic 10 Phase 3: extend ``chat_messages.status`` and
``workflow_node_executions.decision`` CHECK constraints with ``expired``.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0014_hitl_expired_status_checks"
down_revision: Union[str, None] = "0013_background_job_schedules"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_HITL_CHAT_STATUS_CHECK = (
    "status IN ('complete', 'stopped', 'error', 'interrupted', "
    "'waiting_approval', 'rejected')"
)
_EXPIRED_CHAT_STATUS_CHECK = (
    "status IN ('complete', 'stopped', 'error', 'interrupted', "
    "'waiting_approval', 'rejected', 'expired')"
)
_WORKFLOW_DECISION_CHECK = "decision IS NULL OR decision IN ('approved', 'rejected')"
_EXPIRED_WORKFLOW_DECISION_CHECK = (
    "decision IS NULL OR decision IN ('approved', 'rejected', 'expired')"
)


def upgrade() -> None:
    op.drop_constraint("status_valid", "chat_messages", type_="check")
    op.create_check_constraint(
        "status_valid",
        "chat_messages",
        _EXPIRED_CHAT_STATUS_CHECK,
    )

    op.drop_constraint(
        "workflow_node_execution_decision_valid",
        "workflow_node_executions",
        type_="check",
    )
    op.create_check_constraint(
        "workflow_node_execution_decision_valid",
        "workflow_node_executions",
        _EXPIRED_WORKFLOW_DECISION_CHECK,
    )


def downgrade() -> None:
    # Narrower CHECK constraints cannot be restored while expired values remain.
    op.execute(
        "UPDATE workflow_node_executions "
        "SET decision = 'rejected' "
        "WHERE decision = 'expired'"
    )
    op.drop_constraint(
        "workflow_node_execution_decision_valid",
        "workflow_node_executions",
        type_="check",
    )
    op.create_check_constraint(
        "workflow_node_execution_decision_valid",
        "workflow_node_executions",
        _WORKFLOW_DECISION_CHECK,
    )

    op.execute(
        "UPDATE chat_messages SET status = 'error' WHERE status = 'expired'"
    )
    op.drop_constraint("status_valid", "chat_messages", type_="check")
    op.create_check_constraint(
        "status_valid",
        "chat_messages",
        _HITL_CHAT_STATUS_CHECK,
    )
