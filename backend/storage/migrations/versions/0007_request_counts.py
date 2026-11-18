"""Request counts: limits on sign-in attempts, doctor searches and uploads (fastapi-backend Phase 5)

Revision ID: 0007_request_counts
Revises: 0006_workflow_run_updated
Create Date: 2026-11-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_request_counts"
down_revision = "0006_workflow_run_updated"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "request_counts",
        sa.Column("key", sa.String(160), primary_key=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("count", sa.Integer(), nullable=False),
    )
    op.create_index("ix_request_counts_window_start", "request_counts", ["window_start"])


def downgrade() -> None:
    op.drop_index("ix_request_counts_window_start", table_name="request_counts")
    op.drop_table("request_counts")
