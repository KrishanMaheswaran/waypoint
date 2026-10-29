"""add telemetry event idempotency key

Revision ID: 20261029_telemetry_event_id
Revises: 20261026_initial_schema
Create Date: 2026-10-29 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20261029_telemetry_event_id"
down_revision = "20261026_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("telemetry_events", sa.Column("event_id", sa.String(length=100), nullable=True))
    op.execute(
        sa.text(
            "UPDATE telemetry_events "
            "SET event_id = gen_random_uuid()::text "
            "WHERE event_id IS NULL"
        )
    )
    op.alter_column("telemetry_events", "event_id", nullable=False)
    op.create_unique_constraint(
        "uq_telemetry_events_event_id",
        "telemetry_events",
        ["event_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_telemetry_events_event_id", "telemetry_events", type_="unique")
    op.drop_column("telemetry_events", "event_id")
