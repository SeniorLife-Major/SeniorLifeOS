"""Database reads and writes for the document vault. Every read is limited to one owner."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session
from sqlalchemy.orm import selectinload

from ..tools.free_text import FREE_TEXT_FIELDS
from .models import AuditEvent, Document, DocumentField, DocumentPage, DocumentVersion, IdentifierHash, WorkflowRun


def audit(session: Session, actor_id: str, action: str, document_id: str | None, **detail: Any) -> None:
    session.add(AuditEvent(actor_id=actor_id, action=action, document_id=document_id, detail=detail))


def owned_document(session: Session, owner_id: str, document_id: str) -> Document | None:
    """The document with its versions, fields and pages, or None if it isn't this owner's."""
    query = (select(Document)
             .where(Document.id == document_id, Document.owner_id == owner_id)
             .options(selectinload(Document.versions).selectinload(DocumentVersion.fields),
                      selectinload(Document.versions).selectinload(DocumentVersion.pages)))
    return (session.execute(query)).scalar_one_or_none()


def owned_documents(session: Session, owner_id: str, document_type: str | None = None) -> list[Document]:
    query = (select(Document).where(Document.owner_id == owner_id)
             .options(selectinload(Document.versions).selectinload(DocumentVersion.fields))
             .order_by(Document.created_at.desc(), Document.id))
    if document_type:
        query = query.where(Document.document_type == document_type)
    return list((session.execute(query)).scalars())


def vault_identifier_hashes(session: Session, owner_id: str, exclude_document: str | None = None) -> dict[str, str]:
    """keyed hash -> document id, for the owner's confirmed documents (what the graph expects).
    exclude_document: the document being renewed (the same card again is expected there)."""
    query = select(IdentifierHash.digest, IdentifierHash.document_id).where(IdentifierHash.owner_id == owner_id)
    if exclude_document:
        query = query.where(IdentifierHash.document_id != exclude_document)
    return {digest: document_id for digest, document_id in session.execute(query)}


def vault_organization_names(session: Session, owner_id: str) -> dict[str, list[str]]:
    """field -> organization names on the owner's confirmed documents."""
    rows = session.execute(
        select(DocumentField.name, DocumentField.value)
        .join(DocumentVersion, DocumentField.version_id == DocumentVersion.id)
        .join(Document, DocumentVersion.document_id == Document.id)
        .where(Document.owner_id == owner_id, DocumentVersion.status == "confirmed",
               DocumentField.name.in_(FREE_TEXT_FIELDS)))
    names: dict[str, list[str]] = {}
    for field, value in rows:
        if isinstance(value, str) and value not in names.setdefault(field, []):
            names[field].append(value)
    return {field: sorted(values) for field, values in names.items()}


def claim_run(session: Session, run_id: str, from_status: tuple[str, ...]) -> bool:
    """Atomically mark a run as running if it is in one of from_status. False if another
    request got there first (so one review can't be applied twice)."""
    result = session.execute(update(WorkflowRun)
                                   .where(WorkflowRun.id == run_id, WorkflowRun.status.in_(from_status))
                                   .values(status="running"))
    return result.rowcount == 1


def save_confirmed(session: Session, document: Document, version: DocumentVersion,
                   record: dict[str, Any], pages: list[dict[str, Any]]) -> None:
    """Fields, ID hashes and page text of a confirmed version (all already masked by the graph)."""
    for name, field in record["confirmed_fields"].items():
        session.add(DocumentField(version_id=version.id, name=name, value=field["value"],
                                  source_ref=field.get("source_ref"), reviewed_by=field.get("reviewed_by"),
                                  corrected_by_user=bool(field.get("corrected_by_user"))))
    for field, digest in record.get("identifier_hashes", {}).items():
        session.add(IdentifierHash(owner_id=document.owner_id, field=field, digest=digest,
                                   document_id=document.id, version_id=version.id))
    for number, page in enumerate(pages, start=1):
        session.add(DocumentPage(version_id=version.id, page=page.get("page", number), text=page.get("text", "")))
    version.issue_date = as_date(record.get("issue_date"))
    version.expiry_date = as_date(record.get("expiry_date"))


def as_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(value) if isinstance(value, str) and value else None
    except ValueError:
        return None
