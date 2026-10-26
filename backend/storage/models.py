"""Database tables for the document vault (PostgreSQL).

What is stored here never holds a full ID number:
- fields of a confirmed version, with ID numbers masked (the graph masks them);
- keyed hashes of ID numbers, to spot the same card uploaded twice;
- page text of a confirmed version, masked;
- status, review flags (which never quote values) and events.

Fields waiting for review (they still hold full numbers, so the owner can compare them
with the card) and unmasked page text stay only in the encrypted checkpoint.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, list[Any]: JSONB}


def vault_tables_only(name: str | None, type_: str, parent_names: dict[str, Any]) -> bool:
    """For Alembic: compare only these tables. LangGraph's checkpoint tables live in the same
    database but are created by the checkpointer, so a migration must never drop them."""
    return type_ != "table" or name in Base.metadata.tables or name == "alembic_version"


def created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    filename: Mapped[str] = mapped_column(String(255))  # ID numbers in the name are masked
    mime_type: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str | None] = mapped_column(String(32), unique=True)  # None once the file is removed
    document_type: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())

    versions: Mapped[list[DocumentVersion]] = relationship(back_populates="document", order_by="DocumentVersion.version",
                                                           cascade="all, delete-orphan")

    __table_args__ = (Index("ix_documents_owner_type", "owner_id", "document_type"),)


class DocumentVersion(Base):
    __tablename__ = "document_versions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))
    document_type: Mapped[str | None] = mapped_column(String(64))
    run_id: Mapped[str | None] = mapped_column(String(36))
    review_flags: Mapped[list[Any]] = mapped_column(default=list)
    warnings: Mapped[list[Any]] = mapped_column(default=list)
    errors: Mapped[list[Any]] = mapped_column(default=list)
    events: Mapped[list[Any]] = mapped_column(default=list)
    reviewed_by: Mapped[str | None] = mapped_column(String(128))
    issue_date: Mapped[date | None] = mapped_column(Date)
    expiry_date: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = created()
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    document: Mapped[Document] = relationship(back_populates="versions")
    fields: Mapped[list[DocumentField]] = relationship(order_by="DocumentField.name", cascade="all, delete-orphan")
    pages: Mapped[list[DocumentPage]] = relationship(order_by="DocumentPage.page", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("document_id", "version", name="uq_document_versions_document_version"),)


class DocumentField(Base):
    __tablename__ = "document_fields"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    value: Mapped[Any] = mapped_column(JSONB, nullable=True)  # text, number, date or list of rows; IDs masked
    source_ref: Mapped[str | None] = mapped_column(String(255))
    reviewed_by: Mapped[str | None] = mapped_column(String(128))
    corrected_by_user: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (UniqueConstraint("version_id", "name", name="uq_document_fields_version_name"),)


class IdentifierHash(Base):
    """Keyed hash (HMAC-SHA256) of an ID number on a confirmed document. Cannot be reversed
    without SENIORLIFE_HASH_KEY. Looked up only within the same owner's documents."""

    __tablename__ = "identifier_hashes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    owner_id: Mapped[str] = mapped_column(String(128))
    field: Mapped[str] = mapped_column(String(64))
    digest: Mapped[str] = mapped_column(String(64))
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id", ondelete="CASCADE"))

    __table_args__ = (Index("ix_identifier_hashes_owner_digest", "owner_id", "digest"),)


class DocumentPage(Base):
    __tablename__ = "document_pages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id", ondelete="CASCADE"), index=True)
    page: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)  # ID numbers masked

    __table_args__ = (UniqueConstraint("version_id", "page", name="uq_document_pages_version_page"),)


class WorkflowRun(Base):
    """One run of the document graph. Its id is the LangGraph thread id of the encrypted
    checkpoint. status: running, paused (waiting for the owner), ended, failed."""

    __tablename__ = "workflow_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = created()
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    """Who did what to which document. detail never holds field values.
    No foreign key, so the history stays if a document is removed."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = created()
    actor_id: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(64))
    document_id: Mapped[str | None] = mapped_column(String(36), index=True)
    detail: Mapped[dict[str, Any]] = mapped_column(default=dict)
