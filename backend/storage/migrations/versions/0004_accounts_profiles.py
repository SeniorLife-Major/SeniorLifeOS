"""Accounts and profiles: sign-in, language, home, accessibility, consent (fastapi-backend Phase 1)

Revision ID: 0004_accounts_profiles
Revises: 0003_versions_hold_files
Create Date: 2026-11-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_accounts_profiles"
down_revision = "0003_versions_hold_files"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("login", sa.String(254), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "profiles",
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("language", sa.String(8), nullable=False, server_default="en"),
        sa.Column("home_label", sa.String(200), nullable=True),
        sa.Column("home_lat", sa.Float(), nullable=True),
        sa.Column("home_lon", sa.Float(), nullable=True),
        sa.Column("accessibility", postgresql.JSONB(), nullable=False),
        sa.Column("consents", postgresql.JSONB(), nullable=False),
        sa.Column("consents_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("profiles")
    op.drop_table("users")
