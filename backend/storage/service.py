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

A confirmed document can be renewed with a newer version, uploaded or typed in (Decision
#43). The older version stays in use until the newer one is confirmed; then the older one
is marked "replaced" and its fields, page text, ID hashes and file are removed. The owner
can delete a document with everything stored for it.

An upload can also be read in the background (background=True, fastapi-backend Phase 3): the
call returns at once with the document "processing", and BACKGROUND_WORKERS threads read the
waiting uploads in turn (at most BACKGROUND_SLOTS per server, read or waiting; more are refused
with "busy"). The outcome is the same as a normal upload's, seen with get(). A run left
"queued" or "running" by a server that stopped is tidied by fail_stuck_runs.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Any
from uuid import uuid4

from langgraph.types import Command
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from ..errors import AppException, DocumentNotFoundException, NotFoundException, ValidationException
from ..common.request_id import in_this_request
from ..core.config import LimitsSection
from ..errors.exceptions import ConflictException, TooManyRequestsException
from ..errors.error_codes import ErrorCode
from ..graphs.document_graph import (
    ALLOWED_MIME_TYPES,
    MAX_UPLOAD_BYTES,
    REQUIRED_FIELDS,
    build_document_graph,
    missing_optional,
    missing_required,
)
from ..tools.document_rules import DOCUMENT_TYPES, PROOFS, match_proofs, match_requirements
from ..utils.checkpoints import forget_run
from ..utils.privacy import mask_ids_in_text
from .files import EncryptedFileStore, StoredFileError
from .limits import count, wait_words
from .models import Document, DocumentVersion, IdentifierHash, WorkflowRun
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
IN_PROGRESS = ("processing", "needs_review")
ACTIVE_RUNS = ("queued", "running")
BACKGROUND_WORKERS = 2  # uploads read at the same time in the background, per server
BACKGROUND_SLOTS = 6  # background uploads being read or waiting, per server
# A run "queued" or "running" this long was left by a server that stopped. A reading never takes
# this long: a PDF has at most 20 pages, every outside call has a timeout, and a waiting upload
# has at most BACKGROUND_SLOTS - 1 ahead of it.
STUCK_MINUTES = 30
TIDY_EVERY_SECONDS = 60  # how often a server looks for such runs (on a document call)
INTERRUPTED = "Reading this document was interrupted; please add it again"


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


INDIA = timezone(timedelta(hours=5, minutes=30))  # no daylight saving, so this is exact


def india_today() -> date:
    """Today's date in India, whatever the server's own time zone."""
    return datetime.now(INDIA).date()


def current_version(document: Document) -> DocumentVersion | None:
    """The version in use: the newest confirmed one, else the newest (a first reading)."""
    confirmed = [version for version in document.versions if version.status == "confirmed"]
    return confirmed[-1] if confirmed else (document.versions[-1] if document.versions else None)


class WorkflowFailed(AppException):
    def __init__(self, message: str) -> None:
        super().__init__(message=message, error_code=ErrorCode.WORKFLOW_EXECUTION_FAILED, status_code=500)


class ReaderBusy(AppException):
    def __init__(self) -> None:
        super().__init__(message="Many documents are being read right now; please try again in a few minutes",
                         error_code="READER_BUSY", status_code=503)


class DocumentService:
    def __init__(self, sessions: sessionmaker, files: EncryptedFileStore, checkpointer: Any,
                 limits: LimitsSection | None = None):
        self.sessions = sessions
        self.files = files
        self.checkpointer = checkpointer
        self.limits = limits or LimitsSection()
        self.graph = build_document_graph(checkpointer=checkpointer)  # refuses an unencrypted one
        # Typed-in documents never pause (typing in is the owner's confirmation), so nothing is
        # checkpointed: the typed values exist only in memory for one call.
        self.manual_graph = build_document_graph()
        self.background = ThreadPoolExecutor(BACKGROUND_WORKERS, thread_name_prefix="document-reader")
        self._slots = threading.BoundedSemaphore(BACKGROUND_SLOTS)
        self._tidied_at: float | None = None  # never yet: the first document call after start tidies

    # ---- upload --------------------------------------------------------------------------

    def upload(self, owner_id: str, filename: str, mime_type: str, data: bytes, *,
               claimed_type: str | None = None, owner_name: str | None = None,
               cloud_ocr_consent: bool = False, medical_cloud_consent: bool = False,
               use_llm: bool = False, background: bool = False) -> dict[str, Any]:
        """background=True: returns at once with the document "processing" (see the module notes)."""
        if claimed_type is not None and claimed_type not in REQUIRED_FIELDS:
            raise ValidationException("Unknown document type")
        return self._read_new_version(owner_id, None, filename, mime_type, data, claimed_type, owner_name,
                                      {"cloud_ocr_consent": cloud_ocr_consent,
                                       "medical_cloud_consent": medical_cloud_consent, "use_llm": use_llm},
                                      background)

    def replace_upload(self, owner_id: str, document_id: str, filename: str, mime_type: str, data: bytes, *,
                       owner_name: str | None = None, cloud_ocr_consent: bool = False,
                       medical_cloud_consent: bool = False, use_llm: bool = False,
                       background: bool = False) -> dict[str, Any]:
        """A newer copy (e.g. a renewed certificate) of a confirmed document, read and reviewed
        like an upload. The document type stays the same."""
        return self._read_new_version(owner_id, document_id, filename, mime_type, data, None, owner_name,
                                      {"cloud_ocr_consent": cloud_ocr_consent,
                                       "medical_cloud_consent": medical_cloud_consent, "use_llm": use_llm},
                                      background)

    def _read_new_version(self, owner_id: str, document_id: str | None, filename: str, mime_type: str,
                          data: bytes, claimed_type: str | None, owner_name: str | None,
                          options: dict[str, bool], background: bool) -> dict[str, Any]:
        if mime_type not in ALLOWED_MIME_TYPES:
            raise ValidationException("Only PDF, PNG and JPEG files can be uploaded")
        if not 0 < len(data) <= MAX_UPLOAD_BYTES:
            raise ValidationException("File must be between 1 byte and 5 MB")
        with self.sessions.begin() as session:  # limits.uploads_per_hour per person, before anything is stored
            wait = count(session, f"upload:{owner_id}", self.limits.uploads_per_hour, timedelta(hours=1))
        if wait is not None:
            raise TooManyRequestsException(
                f"Many documents were added in the last hour. Please wait {wait_words(wait)} and try again.", wait)
        self._tidy()
        if background and not self._slots.acquire(blocking=False):
            raise ReaderBusy()  # before anything is stored
        try:
            document_id, run_id, storage_key, state = self._add_version(
                owner_id, document_id, filename, mime_type, data, claimed_type, owner_name, options,
                "queued" if background else "running")
        except BaseException:
            if background:
                self._slots.release()
            raise
        suffix = SUFFIXES[mime_type]
        if not background:
            return self._read(owner_id, document_id, run_id, storage_key, suffix, state)
        try:
            started = self.get(owner_id, document_id)  # before a reader can change it: always "processing"
            self.background.submit(in_this_request(self._read_in_background, owner_id, document_id, run_id,
                                                   storage_key, suffix, state))  # logs keep the upload's ID
        except Exception as exc:  # the database, or the reader pool (the server is shutting down)
            self._slots.release()
            self._record_failure(owner_id, document_id, run_id, exc)
            raise WorkflowFailed("The document could not be processed") from None
        return started

    def _add_version(self, owner_id: str, document_id: str | None, filename: str, mime_type: str, data: bytes,
                     claimed_type: str | None, owner_name: str | None, options: dict[str, bool],
                     run_status: str) -> tuple[str, str, str, dict[str, Any]]:
        """Stores the file and records the new version ("processing") and its run (run_status).
        Returns (document id, run id, storage key, the graph's input)."""
        if document_id is not None:
            with self.sessions() as session:
                self._replaceable(session, owner_id, document_id)  # checked before the file is stored
        name = safe_filename(filename)
        stored = self.files.save(data)
        run_id = str(uuid4())
        try:
            with self.sessions.begin() as session:
                if document_id is None:
                    document_id, number, action = str(uuid4()), 1, "document.upload"
                    session.add(Document(id=document_id, owner_id=owner_id, document_type=claimed_type,
                                         status="processing"))
                else:
                    document = self._replaceable(session, owner_id, document_id)
                    claimed_type = document.document_type
                    number, action = document.versions[-1].version + 1, "document.replace"
                version = DocumentVersion(document_id=document_id, version=number, status="processing",
                                          document_type=claimed_type, run_id=run_id, entry="upload", filename=name,
                                          mime_type=mime_type, size_bytes=stored.size_bytes, sha256=stored.sha256,
                                          storage_key=stored.storage_key)
                session.add(version)
                session.flush()
                session.add(WorkflowRun(id=run_id, document_id=document_id, version_id=version.id, status=run_status))
                audit(session, owner_id, action, document_id, version=number, mime_type=mime_type,
                      size_bytes=stored.size_bytes, sha256=stored.sha256)
                hashes = vault_identifier_hashes(session, owner_id, exclude_document=document_id)
                names = vault_organization_names(session, owner_id)
        except IntegrityError:
            self.files.delete(stored.storage_key)
            raise ConflictException("A newer version of this document is already being added") from None
        except Exception:
            self.files.delete(stored.storage_key)
            raise
        state = {
            "actor_id": owner_id,
            "document_id": document_id,
            "document": {"owner_id": owner_id, "filename": name, "mime_type": mime_type,
                         **({"owner_name": owner_name} if owner_name else {})},
            "use_interrupt": True,
            **options,
            "vault_identifier_hashes": hashes,
            "vault_organization_names": names,
            **({"claimed_type": claimed_type} if claimed_type else {}),
        }
        return document_id, run_id, stored.storage_key, state

    def _read(self, owner_id: str, document_id: str, run_id: str, storage_key: str, suffix: str,
              state: dict[str, Any]) -> dict[str, Any]:
        try:
            result = self._run_upload(storage_key, suffix, state, run_id)
        except Exception as exc:
            self._record_failure(owner_id, document_id, run_id, exc)
            raise WorkflowFailed("The document could not be processed") from None
        return self._save_outcome(owner_id, document_id, run_id, result)

    def _read_in_background(self, owner_id: str, document_id: str, run_id: str, storage_key: str, suffix: str,
                            state: dict[str, Any]) -> None:
        """In a reader thread, when its turn comes. Nothing is raised (no one is waiting): the
        outcome, or the failure, is saved on the document."""
        try:
            with self.sessions.begin() as session:
                started = claim_run(session, run_id, ("queued",))  # updated_at: when reading started
            if started:  # else deleted, or given up (fail_stuck_runs), while it waited
                self._read(owner_id, document_id, run_id, storage_key, suffix, state)
        except WorkflowFailed:
            pass  # already recorded on the document
        except Exception as exc:
            logger.error("A background reading could not be finished: %s", type(exc).__name__)
        finally:
            self._slots.release()

    def _replaceable(self, session: Any, owner_id: str, document_id: str) -> Document:
        """The owner's confirmed document, with no newer version being read or reviewed."""
        document = owned_document(session, owner_id, document_id)
        if document is None:
            raise DocumentNotFoundException(document_id)
        if document.versions[-1].status in IN_PROGRESS:
            raise ConflictException("A newer version is already being read or waiting for review; "
                                    "finish or reject it first")
        if not any(version.status == "confirmed" for version in document.versions):
            raise ConflictException("Only a confirmed document can be replaced; add it as a new document instead")
        return document

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
        return self._type_in(owner_id, None, document_type, fields, owner_name)

    def replace_manual(self, owner_id: str, document_id: str, fields: dict[str, Any], *,
                       owner_name: str | None = None) -> dict[str, Any]:
        """A newer version of a confirmed document, typed in. The document type stays the same."""
        with self.sessions() as session:
            document_type = self._replaceable(session, owner_id, document_id).document_type
        return self._type_in(owner_id, document_id, document_type, fields, owner_name)

    def _type_in(self, owner_id: str, document_id: str | None, document_type: str, fields: dict[str, Any],
                 owner_name: str | None) -> dict[str, Any]:
        if not isinstance(fields, dict) or not fields:
            raise ValidationException("Type in at least one field")
        new_document = document_id is None
        document_id = document_id or str(uuid4())
        with self.sessions() as session:
            hashes = vault_identifier_hashes(session, owner_id, exclude_document=document_id)
            names = vault_organization_names(session, owner_id)
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
        removed_files: list[str] = []
        try:
            with self.sessions.begin() as session:
                if new_document:
                    document = Document(id=document_id, owner_id=owner_id, document_type=document_type,
                                        status="confirmed")
                    session.add(document)
                    number = 1
                else:
                    document = self._replaceable(session, owner_id, document_id)  # again: it may have changed
                    number = document.versions[-1].version + 1
                version = DocumentVersion(document_id=document_id, version=number, status="confirmed",
                                          document_type=document_type, entry="manual",
                                          review_flags=list(result.get("review_flags", [])),
                                          warnings=list(result.get("warnings", [])), errors=[],
                                          events=list(result.get("events", [])), reviewed_by=owner_id,
                                          confirmed_at=now())
                session.add(version)
                session.flush()
                save_confirmed(session, document, version, result["vault_record"], [])
                if not new_document:
                    removed_files = self._supersede(session, document, version)
                    document.status = "confirmed"
                audit(session, owner_id, "document.manual_entry" if new_document else "document.replace_manual",
                      document_id, version=number, fields=sorted(fields))
        except IntegrityError:
            raise ConflictException("A newer version of this document is already being added") from None
        self._delete_files(removed_files)
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
        known = document_type in REQUIRED_FIELDS
        return {"document_id": document_id, "document_type": document_type, "candidates": candidates,
                "missing_fields": missing_required(document_type, candidates) if known else [],
                "optional_fields": missing_optional(document_type, candidates) if known else [],
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
                run = session.get(WorkflowRun, run_id, with_for_update=True)
                if run is not None and run.status == "running":  # not given up meanwhile (fail_stuck_runs)
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
        if document.versions[-1].status != "needs_review":  # the newest version (a first reading or a renewal)
            raise ConflictException("This document is not waiting for review")
        return document

    # ---- deleting ------------------------------------------------------------------------

    def delete(self, owner_id: str, document_id: str) -> None:
        """Remove the document and everything stored for it: all versions, fields, page text,
        ID hashes, runs, paused checkpoints and files. The audit trail keeps that it was
        deleted (audit events hold no values). A background upload still waiting for its turn
        can be deleted; one being read can't."""
        self._tidy()
        with self.sessions.begin() as session:
            document = owned_document(session, owner_id, document_id)
            if document is None:
                raise DocumentNotFoundException(document_id)
            runs = session.execute(select(WorkflowRun.id, WorkflowRun.status)
                                   .where(WorkflowRun.document_id == document_id)).all()
            for run_id, run_status in runs:
                # A paused review (or a waiting upload) is taken first, so a review sent (or a reader
                # starting) at the same moment can't resume it.
                if run_status == "running" or (run_status in ("paused", "queued")
                                               and not claim_run(session, run_id, (run_status,))):
                    raise ConflictException("This document is being read right now; try again in a moment")
            files = [version.storage_key for version in document.versions if version.storage_key]
            versions = len(document.versions)
            session.delete(document)  # versions, fields, pages, ID hashes and runs go with it
            audit(session, owner_id, "document.deleted", document_id, versions=versions)
        for run_id, _ in runs:
            try:
                forget_run(self.checkpointer, run_id)
            except Exception as exc:  # the document is gone; an encrypted checkpoint left behind is logged
                logger.error("A deleted document's checkpoint could not be removed: %s", type(exc).__name__)
        self._delete_files(files)

    def _delete_files(self, storage_keys: list[str]) -> None:
        for key in storage_keys:
            try:
                self.files.delete(key)
            except OSError as exc:  # encrypted, under a random name: harmless but logged
                logger.error("A stored file could not be removed: %s", type(exc).__name__)

    # ---- reading -------------------------------------------------------------------------

    def get(self, owner_id: str, document_id: str) -> dict[str, Any]:
        self._tidy()  # a background upload is followed with get: one left by a stopped server ends
        with self.sessions() as session:
            document = owned_document(session, owner_id, document_id)
        if document is None:
            raise DocumentNotFoundException(document_id)
        return view(document)

    def list(self, owner_id: str, document_type: str | None = None) -> list[dict[str, Any]]:
        with self.sessions() as session:
            return [view(document, with_fields=False) for document in owned_documents(session, owner_id,
                                                                                           document_type)]

    def match(self, owner_id: str, requirements: list[str], proofs: list[str],
              as_of: date | None = None) -> dict[str, Any]:
        """Which of the owner's documents meet a list of needs (e.g. a scheme's), by the shared
        rules in tools/document_rules (fastapi-backend Phase 3): for each document type, and for
        each proof (e.g. proof_of_age, which some types can give), the best document and whether
        it is usable, unconfirmed (being read or waiting for review), expired on as_of (default:
        today in India) or missing. Reads only."""
        requirements, proofs = list(dict.fromkeys(requirements)), list(dict.fromkeys(proofs))  # once each
        if not requirements and not proofs:
            raise ValidationException("Name at least one document type or proof")
        if any(name not in DOCUMENT_TYPES for name in requirements):
            raise ValidationException("Unknown document type; known: " + ", ".join(DOCUMENT_TYPES))
        if any(name not in PROOFS for name in proofs):
            raise ValidationException("Unknown proof; known: " + ", ".join(PROOFS))
        day = as_of or india_today()
        with self.sessions() as session:
            records = [record for document in owned_documents(session, owner_id)
                       if (record := vault_record(document)) is not None]
        return {"as_of": day, "matches": match_requirements(requirements, records, owner_id, day.isoformat()),
                "proofs": match_proofs(proofs, records, owner_id, day.isoformat())}

    def file(self, owner_id: str, document_id: str, version: int | None = None) -> dict[str, Any]:
        """The original file of a version (default: the one in use), for the owner to look at,
        e.g. next to the values during review: {"data", "mime_type", "filename", "version"}.
        Only a version that still holds its file: a replaced, refused or failed version's file was
        removed, and a typed-in version never had one (fastapi-backend Phase 3)."""
        with self.sessions.begin() as session:
            document = owned_document(session, owner_id, document_id)
            if document is None:
                raise DocumentNotFoundException(document_id)  # also for someone else's document
            if version is None:
                chosen = current_version(document)
            else:
                chosen = next((item for item in document.versions if item.version == version), None)
            if chosen is None:
                raise NotFoundException("This document has no such version")
            if chosen.entry == "manual":
                raise NotFoundException("This version was typed in, so it has no file")
            if not chosen.storage_key:
                raise NotFoundException("This version's file is no longer kept")
            found = {"mime_type": chosen.mime_type, "filename": chosen.filename, "version": chosen.version}
            storage_key = chosen.storage_key
            audit(session, owner_id, "document.file_viewed", document_id, version=chosen.version)
        try:
            found["data"] = self.files.read(storage_key)
        except StoredFileError as exc:  # missing or damaged on disk; the message holds no values
            logger.error("A stored file could not be read: %s", exc)
            raise AppException(message="The stored file can't be read", error_code="STORED_FILE_UNREADABLE",
                               status_code=500) from None
        return found

    # ---- saving the outcome --------------------------------------------------------------

    # ---- runs left behind ----------------------------------------------------------------

    def fail_stuck_runs(self) -> int:
        """Runs "queued" or "running" for STUCK_MINUTES: the server reading them stopped (a
        restart, a crash, a replica removed), so the owner would otherwise see "processing" for
        ever and could not delete the document. No reading takes that long, so with several
        servers one still reading is never touched.
        - a first reading or a renewal: failed, its file and checkpoint removed; the owner can
          add it again. A renewal's older version stays in use.
        - a review being applied: waiting for review again (its checkpoint is kept).
        Returns how many runs were tidied."""
        failed: list[str] = []
        files: list[str] = []
        with self.sessions.begin() as session:
            stuck = session.scalars(
                select(WorkflowRun)
                .where(WorkflowRun.status.in_(ACTIVE_RUNS),
                       WorkflowRun.updated_at < func.now() - timedelta(minutes=STUCK_MINUTES))
                .with_for_update(skip_locked=True)).all()  # a run being saved right now is left alone
            for run in stuck:
                version = session.get(DocumentVersion, run.version_id)
                document = version.document
                if version.status == "needs_review":
                    run.status = "paused"
                    audit(session, document.owner_id, "document.review_interrupted", document.id, run_id=run.id,
                          version=version.version)
                    continue
                run.status, run.ended_at = "failed", now()
                version.status, version.errors = "failed_tool", [INTERRUPTED]
                if version.storage_key:
                    files.append(version.storage_key)
                    version.storage_key = None
                document.status = current_version(document).status
                audit(session, document.owner_id, "document.failed", document.id, run_id=run.id,
                      version=version.version, error="Interrupted")
                failed.append(run.id)
        for run_id in failed:
            try:
                forget_run(self.checkpointer, run_id)
            except Exception as exc:  # an encrypted checkpoint left behind is logged
                logger.error("A stopped run's checkpoint could not be removed: %s", type(exc).__name__)
        self._delete_files(files)
        if stuck:
            logger.warning("Document runs left by a stopped server were tidied: %d", len(stuck))
        return len(stuck)

    def _tidy(self) -> None:
        """fail_stuck_runs, at most once every TIDY_EVERY_SECONDS per server, before a document
        call; the first call after the server starts always does it."""
        if self._tidied_at is not None and time.monotonic() - self._tidied_at < TIDY_EVERY_SECONDS:
            return
        self._tidied_at = time.monotonic()
        try:
            self.fail_stuck_runs()
        except Exception as exc:  # never stops the call itself; tried again next time
            self._tidied_at = None
            logger.error("Runs left by a stopped server could not be tidied: %s", type(exc).__name__)

    def _supersede(self, session: Any, document: Document, newer: DocumentVersion) -> list[str]:
        """Older confirmed versions become "replaced": their fields, page text, ID hashes and
        file are removed. Returns the files to delete once the transaction is saved."""
        files = []
        for older in document.versions:
            if older is newer or older.version >= newer.version or older.status != "confirmed":
                continue
            older.status = "replaced"
            older.fields.clear()
            older.pages.clear()
            session.execute(delete(IdentifierHash).where(IdentifierHash.version_id == older.id))
            if older.storage_key:
                files.append(older.storage_key)
                older.storage_key = None
        return files

    def _save_outcome(self, actor_id: str, document_id: str, run_id: str,
                      result: dict[str, Any]) -> dict[str, Any]:
        paused = "__interrupt__" in result
        status = "needs_review" if paused else result.get("status") or "failed_tool"
        waiting = status == "needs_review"  # paused at review, or a refused decision to correct
        removed_files: list[str] = []
        with self.sessions.begin() as session:
            run = session.get(WorkflowRun, run_id, with_for_update=True)
            if run is None or run.status != "running":  # given up (fail_stuck_runs) or deleted while read
                return self._dropped(session, actor_id, document_id, run_id, run)
            document = owned_document(session, actor_id, document_id)
            version = document.versions[-1]
            version.status = status
            version.document_type = result.get("document_type") or version.document_type
            version.review_flags = list(result.get("review_flags", []))
            version.warnings = list(result.get("warnings", []))
            version.errors = list(result.get("errors", []))
            version.events = list(result.get("events", []))
            if status == "confirmed":
                version.reviewed_by = result["review_decision"]["reviewer_id"]
                version.confirmed_at = now()
                save_confirmed(session, document, version, result["vault_record"],
                               result.get("document", {}).get("pages", []))
                removed_files += self._supersede(session, document, version)
            run.status = "paused" if waiting else "ended"
            if not waiting:
                run.ended_at = now()
            if not waiting and status != "confirmed" and version.storage_key:
                removed_files.append(version.storage_key)
                version.storage_key = None
            current = current_version(document)
            document.status, document.document_type = current.status, current.document_type
            audit(session, actor_id, f"document.{status}", document_id, run_id=run_id, version=version.version)
        if not waiting:
            forget_run(self.checkpointer, run_id)
        self._delete_files(removed_files)
        return self.get(actor_id, document_id)

    def _dropped(self, session: Any, owner_id: str, document_id: str, run_id: str,
                 run: WorkflowRun | None) -> dict[str, Any]:
        """A reading that ended after its run was given up (fail_stuck_runs) or its document
        deleted: the result is not saved. A review given up is waiting for the owner again, so
        its checkpoint stays; any other checkpoint the late reading wrote is removed."""
        logger.warning("A reading ended after it was given up; its result was not saved")
        if run is None or run.status != "paused":
            forget_run(self.checkpointer, run_id)
        document = owned_document(session, owner_id, document_id)
        if document is None:
            raise DocumentNotFoundException(document_id)
        return view(document)

    def _record_failure(self, actor_id: str, document_id: str, run_id: str, exc: Exception) -> None:
        logger.error("Processing a document failed: %s", type(exc).__name__)  # never the message: it may hold values
        removed_file = None
        with self.sessions.begin() as session:
            run = session.get(WorkflowRun, run_id, with_for_update=True)
            if run is not None and run.status in ACTIVE_RUNS:  # not given up or deleted meanwhile
                document = owned_document(session, actor_id, document_id)
                version = document.versions[-1]
                version.status = "failed_tool"
                version.errors = ["Processing failed"]
                document.status = current_version(document).status
                run.status, run.ended_at = "failed", now()
                removed_file, version.storage_key = version.storage_key, None
                audit(session, actor_id, "document.failed", document_id, run_id=run_id, error=type(exc).__name__)
        try:
            forget_run(self.checkpointer, run_id)
        finally:
            if removed_file:
                self.files.delete(removed_file)


COUNTED_FOR_MATCH = ("confirmed", "processing", "needs_review")


def vault_record(document: Document) -> dict[str, Any] | None:
    """The document as the graphs see it (the "vault record" document_graph publishes), rebuilt
    from the database: the version in use, its confirmed fields (already masked) and dates.
    Only documents that are, or may still become, usable count: confirmed, being read, or
    waiting for review. A rejected or failed one, or one of unknown type, is None (the owner
    has to add it again), so it is reported missing, not "unconfirmed"."""
    version = current_version(document)
    if version is None or version.status not in COUNTED_FOR_MATCH or not document.document_type:
        return None
    confirmed = version.status == "confirmed"
    return {
        "document_id": document.id,
        "owner_id": document.owner_id,
        "type": document.document_type,
        "status": version.status,
        "confirmed_fields": {field.name: {"value": field.value, "source_ref": field.source_ref,
                                          "reviewed_by": field.reviewed_by,
                                          "corrected_by_user": field.corrected_by_user}
                             for field in version.fields} if confirmed else {},
        "issue_date": version.issue_date.isoformat() if version.issue_date else None,
        "expiry_date": version.expiry_date.isoformat() if version.expiry_date else None,
    }


def view(document: Document, with_fields: bool = True) -> dict[str, Any]:
    """The version in use; a newer version being read, reviewed or refused is shown under
    "replacement" (the version in use stays until a newer one is confirmed)."""
    version = current_version(document)
    newest = document.versions[-1] if document.versions else None
    result = {
        "document_id": document.id,
        "entry": version.entry if version else "upload",
        "filename": version.filename if version else None,
        "mime_type": version.mime_type if version else None,
        "size_bytes": version.size_bytes if version else None,
        "document_type": document.document_type,
        "status": document.status,
        "version": version.version if version else None,
        "file_stored": bool(version and version.storage_key),
        "issue_date": version.issue_date if version else None,
        "expiry_date": version.expiry_date if version else None,
        "created_at": document.created_at,
        "updated_at": document.updated_at,
        "replacement": None,
    }
    if newest is not None and newest is not version:
        result["replacement"] = {"version": newest.version, "entry": newest.entry, "status": newest.status}
        if with_fields:
            result["replacement"].update({"review_flags": list(newest.review_flags),
                                          "warnings": list(newest.warnings), "errors": list(newest.errors)})
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
