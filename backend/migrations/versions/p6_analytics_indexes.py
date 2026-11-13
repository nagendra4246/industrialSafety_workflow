"""index alarm cluster partition and ordering

Revision ID: p6_analytics_indexes
Revises: p5_workflow_list_indexes
Create Date: 2026-11-13
"""

from typing import Sequence, Union

from alembic import op


revision: str = "p6_analytics_indexes"
down_revision: Union[str, None] = "p5_workflow_list_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_alarm_event_machine_definition_time",
        "alarm_event",
        ["machine_id", "alarm_definition_id", "event_at", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_alarm_event_machine_definition_time",
        table_name="alarm_event",
    )
