"""Document runs: when the status last changed, to find runs left by a stopped server (fastapi-backend Phase 3)

Revision ID: 0006_workflow_run_updated
Revises: 0005_graph_runs
Create Date: 2026-11-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_workflow_run_updated"
down_revision = "0005_graph_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("workflow_runs", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                                             server_default=sa.func.now()))
    # Existing runs: the last time they are known to have changed.
    op.execute("UPDATE workflow_runs SET updated_at = COALESCE(ended_at, started_at)")
    op.create_index("ix_workflow_runs_status_updated", "workflow_runs", ["status", "updated_at"])


def downgrade() -> None:
    op.drop_index("ix_workflow_runs_status_updated", table_name="workflow_runs")
    op.drop_column("workflow_runs", "updated_at")
