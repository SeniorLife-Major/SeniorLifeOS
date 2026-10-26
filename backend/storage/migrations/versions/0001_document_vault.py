"""Document vault tables

Revision ID: 0001_document_vault
Revises:
Create Date: 2026-10-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_document_vault"
down_revision = None
branch_labels = None
depends_on = None


def timestamp(name: str, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable,
                     server_default=None if nullable else sa.text("now()"))


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(32), nullable=True, unique=True),
        sa.Column("document_type", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        timestamp("created_at"),
        timestamp("updated_at"),
    )
    op.create_index("ix_documents_owner_id", "documents", ["owner_id"])
    op.create_index("ix_documents_owner_type", "documents", ["owner_id", "document_type"])

    op.create_table(
        "document_versions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("document_type", sa.String(64), nullable=True),
        sa.Column("run_id", sa.String(36), nullable=True),
        sa.Column("review_flags", postgresql.JSONB(), nullable=False),
        sa.Column("warnings", postgresql.JSONB(), nullable=False),
        sa.Column("errors", postgresql.JSONB(), nullable=False),
        sa.Column("events", postgresql.JSONB(), nullable=False),
        sa.Column("reviewed_by", sa.String(128), nullable=True),
        sa.Column("issue_date", sa.Date(), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        timestamp("created_at"),
        timestamp("confirmed_at", nullable=True),
        sa.UniqueConstraint("document_id", "version", name="uq_document_versions_document_version"),
    )
    op.create_index("ix_document_versions_document_id", "document_versions", ["document_id"])

    op.create_table(
        "document_fields",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("version_id", sa.BigInteger(), sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("value", postgresql.JSONB(), nullable=True),
        sa.Column("source_ref", sa.String(255), nullable=True),
        sa.Column("reviewed_by", sa.String(128), nullable=True),
        sa.Column("corrected_by_user", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("version_id", "name", name="uq_document_fields_version_name"),
    )
    op.create_index("ix_document_fields_version_id", "document_fields", ["version_id"])

    op.create_table(
        "identifier_hashes",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("field", sa.String(64), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_id", sa.BigInteger(), sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
                  nullable=False),
    )
    op.create_index("ix_identifier_hashes_owner_digest", "identifier_hashes", ["owner_id", "digest"])

    op.create_table(
        "document_pages",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("version_id", sa.BigInteger(), sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.UniqueConstraint("version_id", "page", name="uq_document_pages_version_page"),
    )
    op.create_index("ix_document_pages_version_id", "document_pages", ["version_id"])

    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("document_id", sa.String(36), sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_id", sa.BigInteger(), sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        timestamp("started_at"),
        timestamp("ended_at", nullable=True),
    )
    op.create_index("ix_workflow_runs_document_id", "workflow_runs", ["document_id"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        timestamp("at"),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("document_id", sa.String(36), nullable=True),
        sa.Column("detail", postgresql.JSONB(), nullable=False),
    )
    op.create_index("ix_audit_events_document_id", "audit_events", ["document_id"])


def downgrade() -> None:
    for table in ("audit_events", "workflow_runs", "document_pages", "identifier_hashes", "document_fields",
                  "document_versions", "documents"):
        op.drop_table(table)
