"""Graph runs: doctor search requests and their results, kept 30 days (fastapi-backend Phase 2)

Revision ID: 0005_graph_runs
Revises: 0004_accounts_profiles
Create Date: 2026-11-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_graph_runs"
down_revision = "0004_accounts_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "graph_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("graph", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("outcome", sa.String(48), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_graph_runs_owner_graph_created", "graph_runs", ["owner_id", "graph", "created_at"])
    op.create_index("ix_graph_runs_status_updated", "graph_runs", ["status", "updated_at"])


def downgrade() -> None:
    op.drop_index("ix_graph_runs_status_updated", table_name="graph_runs")
    op.drop_index("ix_graph_runs_owner_graph_created", table_name="graph_runs")
    op.drop_table("graph_runs")
