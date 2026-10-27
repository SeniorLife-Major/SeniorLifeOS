"""Each version holds its own file, so a renewal can replace a document (Decision #43)

Revision ID: 0003_versions_hold_files
Revises: 0002_manual_entry
Create Date: 2026-10-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_versions_hold_files"
down_revision = "0002_manual_entry"
branch_labels = None
depends_on = None

FILE_COLUMNS = (("filename", sa.String(255)), ("mime_type", sa.String(64)), ("size_bytes", sa.Integer()),
                ("sha256", sa.String(64)))
MOVED = "entry, filename, mime_type, size_bytes, sha256"


def upgrade() -> None:
    op.add_column("document_versions", sa.Column("entry", sa.String(16), nullable=False, server_default="upload"))
    for name, type_ in FILE_COLUMNS:
        op.add_column("document_versions", sa.Column(name, type_, nullable=True))
    op.add_column("document_versions", sa.Column("storage_key", sa.String(32), nullable=True))
    op.create_unique_constraint("document_versions_storage_key_key", "document_versions", ["storage_key"])
    # Until now every document had one file, kept on the document: give it to its newest version.
    op.execute(f"""
        UPDATE document_versions AS v
        SET ({MOVED}) = (d.entry, d.filename, d.mime_type, d.size_bytes, d.sha256), storage_key = d.storage_key
        FROM documents AS d
        WHERE v.document_id = d.id
          AND v.version = (SELECT max(version) FROM document_versions WHERE document_id = d.id)
    """)
    for name in ("storage_key", "sha256", "size_bytes", "mime_type", "filename", "entry"):
        op.drop_column("documents", name)


def downgrade() -> None:
    connection = op.get_bind()
    if connection.execute(sa.text("SELECT 1 FROM document_versions WHERE storage_key IS NOT NULL "
                                  "GROUP BY document_id HAVING count(*) > 1")).first():
        # The older file would be lost track of (it stays on disk, encrypted, unused).
        raise RuntimeError("A document has a renewal waiting with two stored files; finish or delete it first")
    op.add_column("documents", sa.Column("entry", sa.String(16), nullable=False, server_default="upload"))
    for name, type_ in FILE_COLUMNS:
        op.add_column("documents", sa.Column(name, type_, nullable=True))
    op.add_column("documents", sa.Column("storage_key", sa.String(32), nullable=True))
    op.create_unique_constraint("documents_storage_key_key", "documents", ["storage_key"])
    op.execute(f"""
        UPDATE documents AS d
        SET ({MOVED}) = (v.entry, v.filename, v.mime_type, v.size_bytes, v.sha256),
            storage_key = (SELECT storage_key FROM document_versions
                           WHERE document_id = d.id AND storage_key IS NOT NULL)
        FROM document_versions AS v
        WHERE v.document_id = d.id
          AND v.version = (SELECT max(version) FROM document_versions WHERE document_id = d.id)
    """)
    op.drop_constraint("document_versions_storage_key_key", "document_versions", type_="unique")
    for name in ("storage_key", "sha256", "size_bytes", "mime_type", "filename", "entry"):
        op.drop_column("document_versions", name)
