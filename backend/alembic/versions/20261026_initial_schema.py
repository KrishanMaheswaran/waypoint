"""create vehicle and telemetry tables

Revision ID: 20261026_initial_schema
Revises:
Create Date: 2026-10-26 00:00:00.000000
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20261026_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vehicles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("vehicle_id", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="idle"),
        sa.Column("battery_percentage", sa.Float(), nullable=False, server_default="0"),
        sa.Column("temperature", sa.Float(), nullable=False, server_default="0"),
        sa.Column("fault_code", sa.String(length=100), nullable=True),
        sa.Column("last_updated", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("vehicle_id"),
    )
    op.create_index(op.f("ix_vehicles_id"), "vehicles", ["id"], unique=False)
    op.create_index(op.f("ix_vehicles_vehicle_id"), "vehicles", ["vehicle_id"], unique=False)

    op.create_table(
        "telemetry_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("vehicle_id", sa.Integer(), nullable=False),
        sa.Column("battery_percentage", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=False),
        sa.Column("fault_code", sa.String(length=100), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicles.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_telemetry_events_id"), "telemetry_events", ["id"], unique=False)
    op.create_index(op.f("ix_telemetry_events_timestamp"), "telemetry_events", ["timestamp"], unique=False)
    op.create_index(op.f("ix_telemetry_events_vehicle_id"), "telemetry_events", ["vehicle_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_telemetry_events_vehicle_id"), table_name="telemetry_events")
    op.drop_index(op.f("ix_telemetry_events_timestamp"), table_name="telemetry_events")
    op.drop_index(op.f("ix_telemetry_events_id"), table_name="telemetry_events")
    op.drop_table("telemetry_events")
    op.drop_index(op.f("ix_vehicles_vehicle_id"), table_name="vehicles")
    op.drop_index(op.f("ix_vehicles_id"), table_name="vehicles")
    op.drop_table("vehicles")
