"""Documents typed in by the owner, with no file (Decision #42)

Revision ID: 0002_manual_entry
Revises: 0001_document_vault
Create Date: 2026-10-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_manual_entry"
down_revision = "0001_document_vault"
branch_labels = None
depends_on = None

FILE_COLUMNS = (("filename", sa.String(255)), ("mime_type", sa.String(64)), ("size_bytes", sa.Integer()),
                ("sha256", sa.String(64)))


def upgrade() -> None:
    op.add_column("documents", sa.Column("entry", sa.String(16), nullable=False, server_default="upload"))
    for name, type_ in FILE_COLUMNS:
        op.alter_column("documents", name, existing_type=type_, nullable=True)


def downgrade() -> None:
    # Refused by the database while typed-in documents exist (their file columns are empty):
    # nothing is deleted to make it pass.
    for name, type_ in FILE_COLUMNS:
        op.alter_column("documents", name, existing_type=type_, nullable=False)
    op.drop_column("documents", "entry")
