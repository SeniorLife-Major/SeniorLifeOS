"""Document vault service: upload -> encrypted file -> document graph -> database.

The graph stays pure (no database access). This service:
1. stores the upload encrypted (storage/files.py);
2. records the document as "processing";
3. runs the document graph on a temporary decrypted copy, with the encrypted PostgreSQL
   checkpointer, passing in the owner's earlier ID hashes and organization names;
4. saves the outcome in one transaction.

A run that pauses for review keeps the candidate values (still with full ID numbers, so
the owner can compare them with the card) only in the encrypted checkpoint. When a run
ends, its checkpoint is deleted. When it ends without a confirmed version (rejected,
unreadable, unknown type, failed), the stored file is deleted too.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any
from uuid import uuid4

from langgraph.types import Command
from sqlalchemy.orm import sessionmaker

from ..errors import AppException, DocumentNotFoundException, ValidationException
from ..errors.exceptions import ConflictException
from ..errors.error_codes import ErrorCode
from ..graphs.document_graph import (
    ALLOWED_MIME_TYPES,
    MAX_UPLOAD_BYTES,
    REQUIRED_FIELDS,
    build_document_graph,
    missing_required,
)
from ..utils.checkpoints import forget_run
from ..utils.privacy import mask_ids_in_text
from .files import EncryptedFileStore
from .models import Document, DocumentVersion, WorkflowRun
from .repository import (
    audit,
    claim_run,
    owned_document,
    owned_documents,
    save_confirmed,
    vault_identifier_hashes,
    vault_organization_names,
)

logger = logging.getLogger(__name__)

SUFFIXES = {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg"}
CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def run_config(run_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": run_id}}


def safe_filename(name: str) -> str:
    """Last path part only, no control characters, ID numbers masked, at most 255 characters."""
    base = PurePosixPath(name.replace("\\", "/")).name
    base = CONTROL.sub("", base).strip()
    if not base.strip("."):  # "." and ".." are not names
        base = ""
    base = mask_ids_in_text(base.replace("_", " ")).replace(" ", "_") if base else base
    return base[:255] or "upload"


def now() -> datetime:
    return datetime.now(timezone.utc)


class WorkflowFailed(AppException):
    def __init__(self, message: str) -> None:
        super().__init__(message=message, error_code=ErrorCode.WORKFLOW_EXECUTION_FAILED, status_code=500)


class DocumentService:
    def __init__(self, sessions: sessionmaker, files: EncryptedFileStore, checkpointer: Any):
        self.sessions = sessions
        self.files = files
        self.checkpointer = checkpointer
        self.graph = build_document_graph(checkpointer=checkpointer)  # refuses an unencrypted one
        # Typed-in documents never pause (typing in is the owner's confirmation), so nothing is
        # checkpointed: the typed values exist only in memory for one call.
        self.manual_graph = build_document_graph()

    # ---- upload --------------------------------------------------------------------------

    def upload(self, owner_id: str, filename: str, mime_type: str, data: bytes, *,
                     claimed_type: str | None = None, owner_name: str | None = None,
                     cloud_ocr_consent: bool = False, medical_cloud_consent: bool = False,
                     use_llm: bool = False) -> dict[str, Any]:
        if mime_type not in ALLOWED_MIME_TYPES:
            raise ValidationException("Only PDF, PNG and JPEG files can be uploaded")
        if not 0 < len(data) <= MAX_UPLOAD_BYTES:
            raise ValidationException("File must be between 1 byte and 5 MB")
        if claimed_type is not None and claimed_type not in REQUIRED_FIELDS:
            raise ValidationException("Unknown document type")
        name = safe_filename(filename)
        stored = self.files.save(data)
        document_id, run_id = str(uuid4()), str(uuid4())
        try:
            with self.sessions.begin() as session:
                session.add(Document(id=document_id, owner_id=owner_id, filename=name, mime_type=mime_type,
                                     size_bytes=stored.size_bytes, sha256=stored.sha256,
                                     storage_key=stored.storage_key, document_type=claimed_type,
                                     status="processing"))
                version = DocumentVersion(document_id=document_id, version=1, status="processing",
                                          document_type=claimed_type, run_id=run_id)
                session.add(version)
                session.flush()
                session.add(WorkflowRun(id=run_id, document_id=document_id, version_id=version.id, status="running"))
                audit(session, owner_id, "document.upload", document_id, mime_type=mime_type,
                      size_bytes=stored.size_bytes, sha256=stored.sha256)
                hashes = vault_identifier_hashes(session, owner_id)
                names = vault_organization_names(session, owner_id)
        except Exception:
            self.files.delete(stored.storage_key)
            raise
        state = {
            "actor_id": owner_id,
            "document_id": document_id,
            "document": {"owner_id": owner_id, "filename": name, "mime_type": mime_type,
                         **({"owner_name": owner_name} if owner_name else {})},
            "use_interrupt": True,
            "use_llm": use_llm,
            "cloud_ocr_consent": cloud_ocr_consent,
            "medical_cloud_consent": medical_cloud_consent,
            "vault_identifier_hashes": hashes,
            "vault_organization_names": names,
            **({"claimed_type": claimed_type} if claimed_type else {}),
        }
        try:
            result = self._run_upload(stored.storage_key, SUFFIXES[mime_type], state, run_id)
        except Exception as exc:
            self._record_failure(owner_id, document_id, run_id, exc)
            raise WorkflowFailed("The document could not be processed") from None
        return self._save_outcome(owner_id, document_id, run_id, result)

    # ---- typed in by the owner -----------------------------------------------------------

    def create_manual(self, owner_id: str, document_type: str, fields: dict[str, Any], *,
                      owner_name: str | None = None) -> dict[str, Any]:
        """A document whose details the owner typed in, with no file (Decision #42).

        The values get the same checks as values read from a page, ID numbers are masked and
        hashed, and the document is saved confirmed in one transaction. Anything invalid is
        refused (422) with the reasons and nothing is stored. Records say entered_by_owner, so
        a scheme that needs the paper itself can still ask for it.
        """
        if document_type not in REQUIRED_FIELDS:
            raise ValidationException("Unknown document type")
        if not isinstance(fields, dict) or not fields:
            raise ValidationException("Type in at least one field")
        with self.sessions() as session:
            hashes = vault_identifier_hashes(session, owner_id)
            names = vault_organization_names(session, owner_id)
        document_id = str(uuid4())
        state = {
            "actor_id": owner_id,
            "document_id": document_id,
            "document": {"owner_id": owner_id, **({"owner_name": owner_name} if owner_name else {})},
            "claimed_type": document_type,
            "manual_fields": fields,
            "vault_identifier_hashes": hashes,
            "vault_organization_names": names,
        }
        try:
            result = self.manual_graph.invoke(state)
        except Exception as exc:
            logger.error("A typed-in document failed: %s", type(exc).__name__)  # never the message
            raise WorkflowFailed("The document could not be saved") from None
        if result.get("status") == "rejected":
            raise ValidationException("Some fields need correcting", details={"errors": result.get("errors", [])})
        if result.get("status") != "confirmed":
            raise WorkflowFailed("The document could not be saved")  # e.g. no hash key: never store a full ID
        with self.sessions.begin() as session:
            document = Document(id=document_id, owner_id=owner_id, entry="manual", document_type=document_type,
                                status="confirmed")
            session.add(document)
            version = DocumentVersion(document_id=document_id, version=1, status="confirmed",
                                      document_type=document_type, review_flags=list(result.get("review_flags", [])),
                                      warnings=list(result.get("warnings", [])), errors=[],
                                      events=list(result.get("events", [])), reviewed_by=owner_id,
                                      confirmed_at=now())
            session.add(version)
            session.flush()
            save_confirmed(session, document, version, result["vault_record"], [])
            audit(session, owner_id, "document.manual_entry", document_id, fields=sorted(fields))
        return self.get(owner_id, document_id)

    def _run_upload(self, storage_key: str, suffix: str, state: dict[str, Any], run_id: str) -> dict[str, Any]:
        with self.files.decrypted_copy(storage_key, suffix) as path:
            state["document"]["file_path"] = str(path)
            return self.graph.invoke(state, run_config(run_id))

    # ---- review --------------------------------------------------------------------------

    def pending_review(self, owner_id: str, document_id: str) -> dict[str, Any]:
        """The values waiting for the owner's review, with full ID numbers (owner only)."""
        with self.sessions.begin() as session:
            document = self._waiting(session, owner_id, document_id)
            run_id = document.versions[-1].run_id
            audit(session, owner_id, "document.review_viewed", document_id)
        snapshot = self.graph.get_state(run_config(run_id))
        values = snapshot.values
        if not values:
            raise WorkflowFailed("The paused run could not be found")
        if snapshot.interrupts:
            payload = snapshot.interrupts[0].value
            candidates, flags = payload["candidates"], payload["flags"]
        else:  # an earlier decision needs correcting
            candidates, flags = values.get("normalized_fields", {}), values.get("review_flags", [])
        document_type = values.get("document_type")
        return {"document_id": document_id, "document_type": document_type, "candidates": candidates,
                "missing_fields": missing_required(document_type, candidates) if document_type in REQUIRED_FIELDS else [],
                "flags": flags, "errors": values.get("errors", [])}

    def review(self, owner_id: str, document_id: str, action: str,
                     edits: dict[str, Any] | None = None) -> dict[str, Any]:
        if action not in ("confirm", "reject"):
            raise ValidationException("action must be confirm or reject")
        with self.sessions.begin() as session:
            document = self._waiting(session, owner_id, document_id)
            run_id = document.versions[-1].run_id
            if not claim_run(session, run_id, ("paused",)):
                raise ConflictException("This review is already being handled")
            audit(session, owner_id, f"document.review_{action}", document_id, edited_fields=sorted(edits or {}))
        decision = {"action": action, "reviewer_id": owner_id, **({"edits": edits} if edits else {})}
        try:
            result = self._resume(run_id, decision)
        except Exception as exc:
            logger.error("Review of a document failed: %s", type(exc).__name__)
            with self.sessions.begin() as session:
                run = session.get(WorkflowRun, run_id)
                run.status = "paused"  # the owner can try again
            raise WorkflowFailed("The review could not be applied; please try again") from None
        return self._save_outcome(owner_id, document_id, run_id, result)

    def _resume(self, run_id: str, decision: dict[str, Any]) -> dict[str, Any]:
        config = run_config(run_id)
        snapshot = self.graph.get_state(config)
        if not snapshot.values:
            raise LookupError("no checkpoint for this run")
        if snapshot.interrupts:
            return self.graph.invoke(Command(resume=decision), config)
        # The last decision was refused (e.g. a required field left empty): give the new one
        # to the review step again. OCR is not run again.
        self.graph.update_state(config, {"review_decision": decision, "route": "review", "errors": []},
                                as_node="confidence_gate")
        return self.graph.invoke(None, config)

    def _waiting(self, session: Any, owner_id: str, document_id: str) -> Document:
        document = owned_document(session, owner_id, document_id)
        if document is None:
            raise DocumentNotFoundException(document_id)  # also for someone else's document
        if document.status != "needs_review":
            raise ConflictException("This document is not waiting for review")
        return document

    # ---- reading -------------------------------------------------------------------------

    def get(self, owner_id: str, document_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            document = owned_document(session, owner_id, document_id)
        if document is None:
            raise DocumentNotFoundException(document_id)
        return view(document)

    def list(self, owner_id: str, document_type: str | None = None) -> list[dict[str, Any]]:
        with self.sessions() as session:
            return [view(document, with_fields=False) for document in owned_documents(session, owner_id,
                                                                                           document_type)]

    # ---- saving the outcome --------------------------------------------------------------

    def _save_outcome(self, actor_id: str, document_id: str, run_id: str,
                            result: dict[str, Any]) -> dict[str, Any]:
        paused = "__interrupt__" in result
        status = "needs_review" if paused else result.get("status") or "failed_tool"
        waiting = status == "needs_review"  # paused at review, or a refused decision to correct
        removed_file = None
        with self.sessions.begin() as session:
            document = owned_document(session, actor_id, document_id)
            version = document.versions[-1]
            run = session.get(WorkflowRun, run_id)
            document_type = result.get("document_type") or version.document_type
            document.status = version.status = status
            document.document_type = version.document_type = document_type
            version.review_flags = list(result.get("review_flags", []))
            version.warnings = list(result.get("warnings", []))
            version.errors = list(result.get("errors", []))
            version.events = list(result.get("events", []))
            if status == "confirmed":
                version.reviewed_by = result["review_decision"]["reviewer_id"]
                version.confirmed_at = now()
                save_confirmed(session, document, version, result["vault_record"],
                               result.get("document", {}).get("pages", []))
            run.status = "paused" if waiting else "ended"
            if not waiting:
                run.ended_at = now()
            if not waiting and status != "confirmed":
                removed_file, document.storage_key = document.storage_key, None
            audit(session, actor_id, f"document.{status}", document_id, run_id=run_id, version=version.version)
        if not waiting:
            forget_run(self.checkpointer, run_id)
        if removed_file:
            self.files.delete(removed_file)
        return self.get(actor_id, document_id)

    def _record_failure(self, actor_id: str, document_id: str, run_id: str, exc: Exception) -> None:
        logger.error("Processing a document failed: %s", type(exc).__name__)  # never the message: it may hold values
        removed_file = None
        with self.sessions.begin() as session:
            document = owned_document(session, actor_id, document_id)
            version = document.versions[-1]
            document.status = version.status = "failed_tool"
            version.errors = ["Processing failed"]
            run = session.get(WorkflowRun, run_id)
            run.status, run.ended_at = "failed", now()
            removed_file, document.storage_key = document.storage_key, None
            audit(session, actor_id, "document.failed", document_id, run_id=run_id, error=type(exc).__name__)
        try:
            forget_run(self.checkpointer, run_id)
        finally:
            if removed_file:
                self.files.delete(removed_file)


def view(document: Document, with_fields: bool = True) -> dict[str, Any]:
    version = document.versions[-1] if document.versions else None
    result = {
        "document_id": document.id,
        "entry": document.entry,
        "filename": document.filename,
        "mime_type": document.mime_type,
        "size_bytes": document.size_bytes,
        "document_type": document.document_type,
        "status": document.status,
        "version": version.version if version else None,
        "file_stored": document.storage_key is not None,
        "issue_date": version.issue_date if version else None,
        "expiry_date": version.expiry_date if version else None,
        "created_at": document.created_at,
        "updated_at": document.updated_at,
    }
    if with_fields:
        result.update({
            "review_flags": list(version.review_flags) if version else [],
            "warnings": list(version.warnings) if version else [],
            "errors": list(version.errors) if version else [],
            "fields": {field.name: {"value": field.value, "source_ref": field.source_ref,
                                    "reviewed_by": field.reviewed_by, "corrected_by_user": field.corrected_by_user}
                       for field in (version.fields if version else [])},
        })
    return result
