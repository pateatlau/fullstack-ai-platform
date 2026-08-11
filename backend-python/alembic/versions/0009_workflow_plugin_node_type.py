"""0009 workflow plugin node type

Revision ID: 0009_workflow_plugin_node_type
Revises: 0008_observability_usage_cost
Create Date: 2026-08-11

Epic 08: allow ``plugin`` in ``workflow_node_executions.node_type`` check constraint.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0009_workflow_plugin_node_type"
down_revision: Union[str, None] = "0008_observability_usage_cost"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NODE_TYPE_CHECK = (
    "node_type IN ('task', 'llm', 'agent', 'router', 'fork', 'join', "
    "'approval', 'terminal', 'plugin')"
)
_LEGACY_NODE_TYPE_CHECK = (
    "node_type IN ('task', 'llm', 'agent', 'router', 'fork', 'join', "
    "'approval', 'terminal')"
)


def upgrade() -> None:
    op.drop_constraint(
        "workflow_node_execution_type_valid",
        "workflow_node_executions",
        type_="check",
    )
    op.create_check_constraint(
        "workflow_node_execution_type_valid",
        "workflow_node_executions",
        _NODE_TYPE_CHECK,
    )


def downgrade() -> None:
    op.drop_constraint(
        "workflow_node_execution_type_valid",
        "workflow_node_executions",
        type_="check",
    )
    op.create_check_constraint(
        "workflow_node_execution_type_valid",
        "workflow_node_executions",
        _LEGACY_NODE_TYPE_CHECK,
    )
