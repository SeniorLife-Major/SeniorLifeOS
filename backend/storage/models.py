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
    Float,
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
    document_type: Mapped[str | None] = mapped_column(String(64))
    # The status of the current version: the newest confirmed one, else the newest (Decision #43).
    status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())

    versions: Mapped[list[DocumentVersion]] = relationship(back_populates="document", order_by="DocumentVersion.version",
                                                           cascade="all, delete-orphan")

    __table_args__ = (Index("ix_documents_owner_type", "owner_id", "document_type"),)


class DocumentVersion(Base):
    """One reading of a document: the first upload, or a renewal that replaces it (Decision #43).
    status: processing, needs_review, confirmed, replaced (a newer version was confirmed; its
    fields, page text, ID hashes and file are removed), or how it ended (rejected, ...)."""

    __tablename__ = "document_versions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))
    # "upload": read from a file; "manual": typed in by the owner, no file (Decision #42).
    entry: Mapped[str] = mapped_column(String(16), default="upload", server_default="upload")
    # The file's details: None for a typed-in version.
    filename: Mapped[str | None] = mapped_column(String(255))  # ID numbers in the name are masked
    mime_type: Mapped[str | None] = mapped_column(String(64))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64))
    storage_key: Mapped[str | None] = mapped_column(String(32), unique=True)  # None once the file is removed
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
    checkpoint. status: queued (a background upload waiting for a reader), running, paused
    (waiting for the owner), ended, failed. updated_at changes whenever the status does, so a
    run left "queued" or "running" by a server that stopped can be found."""

    __tablename__ = "workflow_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("document_versions.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(16))
    started_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_workflow_runs_status_updated", "status", "updated_at"),)


class User(Base):
    """A person who signs in (fastapi-backend Phase 1). id is the owner_id used everywhere else
    (documents, runs, audit). login: an Indian mobile number as +91XXXXXXXXXX or a lower-case
    e-mail. password_hash: scrypt (core/security.py), never the password. token_version: signing out
    adds 1, so every token issued before stops working."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    login: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(16))  # senior or family
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    disabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = created()
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    profile: Mapped[Profile | None] = relationship(back_populates="user", cascade="all, delete-orphan",
                                                    uselist=False)


class Profile(Base):
    """What the app remembers about a person between requests: language, home (rounded to about
    100 m), accessibility and consent choices (fastapi-backend Phase 1)."""

    __tablename__ = "profiles"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    language: Mapped[str] = mapped_column(String(8), default="en", server_default="en")
    home_label: Mapped[str | None] = mapped_column(String(200))
    home_lat: Mapped[float | None] = mapped_column(Float)
    home_lon: Mapped[float | None] = mapped_column(Float)
    accessibility: Mapped[dict[str, Any]] = mapped_column(default=dict)  # font_scale, high_contrast, voice
    consents: Mapped[dict[str, Any]] = mapped_column(default=dict)  # use_llm, medical_cloud, cloud_ocr
    consents_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())

    user: Mapped[User] = relationship(back_populates="profile")


class RequestCount(Base):
    """How many times something was done in the current window (fastapi-backend Phase 5,
    storage/limits.py). key: what and whose, e.g. "upload:<user id>", or "sign-in:" + a keyed
    hash of the login (never the login itself)."""

    __tablename__ = "request_counts"

    key: Mapped[str] = mapped_column(String(160), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    count: Mapped[int] = mapped_column(Integer, default=1)


class GraphRun(Base):
    """One run of a graph other than the document graph: doctor search ("health") now, benefits
    later (fastapi-backend Phase 2). Its id is the LangGraph thread id of the encrypted checkpoint,
    which exists only while the run waits for the person's answer.
    status: running, paused (waiting for an answer), ended, failed, expired (not answered in time).
    outcome: the graph's own status (doctors_found, emergency, needs_location, ...).
    result: what the person was shown, removed RESULT_DAYS after the run ends
    (storage/health_service.py). Never the request text or the phone's location: those stay in
    the encrypted checkpoint, deleted when the run ends."""

    __tablename__ = "graph_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(128))
    graph: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))
    outcome: Mapped[str | None] = mapped_column(String(48))
    result: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = created()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_graph_runs_owner_graph_created", "owner_id", "graph", "created_at"),
                      Index("ix_graph_runs_status_updated", "status", "updated_at"))


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
