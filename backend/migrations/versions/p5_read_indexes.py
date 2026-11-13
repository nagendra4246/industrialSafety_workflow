"""add indexes for workflow list queries

Revision ID: p5_read_indexes
Revises: ab80783181b1
Create Date: 2026-11-13
"""

from typing import Sequence, Union

from alembic import op


revision: str = "p5_read_indexes"
down_revision: Union[str, None] = "ab80783181b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_alarm_event_event_at_id", "alarm_event", ["event_at", "id"]
    )
    op.create_index(
        "ix_machine_state_event_event_at_id",
        "machine_state_event",
        ["event_at", "id"],
    )
    op.create_index(
        "ix_acknowledgement_acknowledged_at_id",
        "acknowledgement",
        ["acknowledged_at", "id"],
    )
    op.create_index(
        "ix_incident_status_opened_at_id",
        "incident",
        ["status", "opened_at", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_incident_status_opened_at_id", table_name="incident")
    op.drop_index(
        "ix_acknowledgement_acknowledged_at_id", table_name="acknowledgement"
    )
    op.drop_index(
        "ix_machine_state_event_event_at_id", table_name="machine_state_event"
    )
    op.drop_index("ix_alarm_event_event_at_id", table_name="alarm_event")
