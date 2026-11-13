"""index incident, action, and lifecycle list ordering

Revision ID: p5_workflow_list_indexes
Revises: p5_read_indexes
Create Date: 2026-11-13
"""

from typing import Sequence, Union

from alembic import op


revision: str = "p5_workflow_list_indexes"
down_revision: Union[str, None] = "p5_read_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_incident_opened_at_id", "incident", ["opened_at", "id"]
    )
    op.create_index(
        "ix_corrective_action_due_at_id",
        "corrective_action",
        ["due_at", "id"],
    )
    op.create_index(
        "ix_lifecycle_history_occurred_at_id",
        "lifecycle_history",
        ["occurred_at", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_lifecycle_history_occurred_at_id",
        table_name="lifecycle_history",
    )
    op.drop_index(
        "ix_corrective_action_due_at_id",
        table_name="corrective_action",
    )
    op.drop_index("ix_incident_opened_at_id", table_name="incident")
