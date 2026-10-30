"""record terminal telemetry ingestion rejections

Revision ID: 20261030_ingestion_failures
Revises: 20261029_telemetry_event_id
Create Date: 2026-10-30 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20261030_ingestion_failures"
down_revision = "20261029_telemetry_event_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telemetry_ingestion_failures",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("topic", sa.String(length=255), nullable=False),
        sa.Column("partition", sa.Integer(), nullable=False),
        sa.Column("offset", sa.BigInteger(), nullable=False),
        sa.Column("event_id", sa.String(length=100), nullable=True),
        sa.Column("reason", sa.String(length=50), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "topic",
            "partition",
            "offset",
            name="uq_telemetry_ingestion_failures_source_offset",
        ),
    )


def downgrade() -> None:
    op.drop_table("telemetry_ingestion_failures")
