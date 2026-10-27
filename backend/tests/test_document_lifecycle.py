"""Tests: deleting a document and replacing it with a newer version (Decision #43).

Storage (DocumentService on the test database), the 0003 migration's data move, and the API.
"""

import unittest
from unittest.mock import patch

from alembic import command
from alembic.config import Config
from sqlalchemy import func, insert, select, text

from backend.api import documents
from backend.errors import DocumentNotFoundException, ValidationException
from backend.errors.exceptions import ConflictException
from backend.storage.models import (
    AuditEvent, Document, DocumentField, DocumentPage, DocumentVersion, IdentifierHash, WorkflowRun,
)
from backend.storage.service import WorkflowFailed
from test_documents_api import DOCUMENT, ApiCase, FakeService
from test_manual_entry import TYPED_IDENTITY, hash_key
from test_storage_db import (
    BACKEND, IDENTITY, OTHER_OWNER, OWNER, UNSURE_IDENTITY, DatabaseCase, drop_everything, migrate,
)

RENEWED = [("Name: Meera Rao", 0.99), ("Date of birth: 1950-09-17", 0.99), ("Aadhaar: 3399 8698 7973", 0.99)]


class LifecycleCase(DatabaseCase):
    def replace(self, document_id, lines, owner=OWNER, **extra):
        with self.ocr(lines):
            return self.service.replace_upload(owner, document_id, "renewed.png", "image/png", self.png, **extra)

    def stored_files(self):
        return sorted(path.name for path in self.root.rglob("*") if path.is_file())

    def count(self, model, **where):
        query = select(func.count()).select_from(model)
        for column, value in where.items():
            query = query.where(getattr(model, column) == value)
        with self.engine.connect() as connection:
            return connection.execute(query).scalar_one()

    def versions(self, document_id):
        with self.engine.connect() as connection:
            return connection.execute(select(DocumentVersion.version, DocumentVersion.status,
                                             DocumentVersion.storage_key.is_not(None))
                                      .where(DocumentVersion.document_id == document_id)
                                      .order_by(DocumentVersion.version)).all()


class DeleteTests(LifecycleCase):
    def test_everything_stored_for_the_document_is_removed(self):
        document_id = self.upload(IDENTITY)["document_id"]
        self.assertEqual(len(self.stored_files()), 1)
        self.service.delete(OWNER, document_id)
        for model in (Document, DocumentVersion, DocumentField, DocumentPage, IdentifierHash, WorkflowRun):
            self.assertEqual(self.count(model), 0, model.__name__)
        self.assertEqual(self.stored_files(), [])
        with self.engine.connect() as connection:
            detail = connection.execute(select(AuditEvent.detail)
                                        .where(AuditEvent.action == "document.deleted")).scalar_one()
        self.assertEqual(detail, {"versions": 1})
        with self.assertRaises(DocumentNotFoundException):
            self.service.get(OWNER, document_id)
        again = self.upload(IDENTITY)  # the deleted card is no longer "already in the vault"
        self.assertFalse(any("already in the vault" in warning for warning in again["warnings"]))

    def test_a_paused_review_and_its_checkpoint_are_removed(self):
        document_id = self.upload(UNSURE_IDENTITY)["document_id"]
        run_id = self.run_id(document_id)
        self.assertNotEqual(self.checkpoint_rows(run_id), [])
        self.service.delete(OWNER, document_id)
        self.assertEqual(self.checkpoint_rows(run_id), [])
        self.assertEqual(self.stored_files(), [])
        with self.assertRaises(DocumentNotFoundException):
            self.review(document_id)

    def test_someone_else_cannot_delete_it(self):
        document_id = self.upload(IDENTITY)["document_id"]
        with self.assertRaises(DocumentNotFoundException):
            self.service.delete(OTHER_OWNER, document_id)
        self.assertEqual(self.service.get(OWNER, document_id)["status"], "confirmed")
        self.assertEqual(len(self.stored_files()), 1)

    def test_a_document_being_read_right_now_is_not_deleted(self):
        document_id = self.upload(UNSURE_IDENTITY)["document_id"]
        for status in ("running", "taken"):
            with self.subTest(status=status):
                if status == "running":
                    sql = "UPDATE workflow_runs SET status = 'running'"
                    patcher = patch("backend.storage.service.claim_run", wraps=lambda *a: True)
                else:  # paused, but a review took it a moment earlier
                    sql = "UPDATE workflow_runs SET status = 'paused'"
                    patcher = patch("backend.storage.service.claim_run", return_value=False)
                with self.engine.begin() as connection:
                    connection.execute(text(sql))
                with patcher, self.assertRaises(ConflictException):
                    self.service.delete(OWNER, document_id)
                self.assertEqual(self.count(Document), 1)
                self.assertEqual(len(self.stored_files()), 1)

    def test_a_checkpoint_that_cannot_be_removed_does_not_undo_the_delete(self):
        document_id = self.upload(UNSURE_IDENTITY)["document_id"]
        with patch("backend.storage.service.forget_run", side_effect=RuntimeError("down")), \
                self.assertLogs("backend.storage.service", "ERROR") as logs:
            self.service.delete(OWNER, document_id)
        self.assertEqual(self.count(Document), 0)
        self.assertEqual(self.stored_files(), [])
        self.assertIn("checkpoint could not be removed: RuntimeError", logs.output[0])

    def test_a_document_with_a_renewal_waiting_loses_both_files(self):
        document_id = self.upload(IDENTITY)["document_id"]
        self.replace(document_id, UNSURE_IDENTITY)
        self.assertEqual(len(self.stored_files()), 2)
        self.service.delete(OWNER, document_id)
        self.assertEqual(self.stored_files(), [])
        self.assertEqual(self.count(DocumentVersion), 0)


class ReplaceTests(LifecycleCase):
    def test_a_sure_renewal_replaces_the_old_version(self):
        first = self.upload(IDENTITY)
        old_file = self.stored_files()
        renewed = self.replace(first["document_id"], RENEWED)
        self.assertEqual((renewed["status"], renewed["version"], renewed["replacement"]), ("confirmed", 2, None))
        self.assertEqual(renewed["document_id"], first["document_id"])
        self.assertFalse(any("already in the vault" in warning for warning in renewed["warnings"]))  # same card
        self.assertEqual(self.versions(first["document_id"]), [(1, "replaced", False), (2, "confirmed", True)])
        self.assertNotEqual(self.stored_files(), old_file)
        self.assertEqual(len(self.stored_files()), 1)
        with self.engine.connect() as connection:
            old = connection.execute(select(DocumentVersion.id).where(DocumentVersion.version == 1)).scalar_one()
        self.assertEqual(self.count(DocumentField, version_id=old), 0)
        self.assertEqual(self.count(DocumentPage, version_id=old), 0)
        self.assertEqual(self.count(IdentifierHash, version_id=old), 0)
        self.assertEqual(self.count(IdentifierHash), 1)  # the new version's
        self.assertNotIn("pan_number", renewed["fields"])  # only what the new copy shows
        self.assert_no_full_numbers(self.database_text())

    def test_the_old_version_stays_in_use_until_the_renewal_is_confirmed(self):
        document_id = self.upload(IDENTITY)["document_id"]
        waiting = self.replace(document_id, UNSURE_IDENTITY)
        self.assertEqual((waiting["status"], waiting["version"]), ("confirmed", 1))
        self.assertEqual(waiting["fields"]["aadhaar_number"]["value"], "XXXX XXXX 7973")
        self.assertEqual(waiting["replacement"]["version"], 2)
        self.assertEqual(waiting["replacement"]["status"], "needs_review")
        self.assertIn("name: low confidence (0.60)", waiting["replacement"]["review_flags"])
        listed = self.service.list(OWNER)[0]
        self.assertEqual(listed["replacement"], {"version": 2, "entry": "upload", "status": "needs_review"})
        with hash_key():
            pending = self.service.pending_review(OWNER, document_id)
        self.assertEqual(pending["candidates"]["name"], "Meera Rao")
        done = self.review(document_id)
        self.assertEqual((done["status"], done["version"], done["replacement"]), ("confirmed", 2, None))
        self.assertEqual(self.versions(document_id), [(1, "replaced", False), (2, "confirmed", True)])

    def test_a_rejected_renewal_leaves_the_old_version_in_use(self):
        document_id = self.upload(IDENTITY)["document_id"]
        self.replace(document_id, UNSURE_IDENTITY)
        result = self.review(document_id, action="reject")
        self.assertEqual((result["status"], result["version"], result["file_stored"]), ("confirmed", 1, True))
        self.assertEqual(result["replacement"]["status"], "rejected")
        self.assertEqual(self.versions(document_id), [(1, "confirmed", True), (2, "rejected", False)])
        self.assertEqual(len(self.stored_files()), 1)
        self.assertEqual(self.count(IdentifierHash), 1)
        again = self.replace(document_id, RENEWED)  # a refused renewal can be tried again
        self.assertEqual(again["version"], 3)

    def test_a_crash_while_reading_the_renewal_keeps_the_old_version(self):
        document_id = self.upload(IDENTITY)["document_id"]
        with patch.object(self.service, "_run_upload", side_effect=RuntimeError("boom")), \
                self.assertRaises(WorkflowFailed):
            self.replace(document_id, RENEWED)
        result = self.service.get(OWNER, document_id)
        self.assertEqual((result["status"], result["version"]), ("confirmed", 1))
        self.assertEqual(result["replacement"]["status"], "failed_tool")
        self.assertEqual(len(self.stored_files()), 1)

    def test_only_a_confirmed_document_with_nothing_waiting_can_be_replaced(self):
        paused = self.upload(UNSURE_IDENTITY)["document_id"]
        with self.assertRaises(ConflictException) as caught:
            self.replace(paused, RENEWED)
        self.assertIn("finish or reject it first", caught.exception.message)
        self.review(paused, action="reject")
        with self.assertRaises(ConflictException) as caught:
            self.replace(paused, RENEWED)
        self.assertIn("Only a confirmed document", caught.exception.message)
        confirmed = self.upload(IDENTITY)["document_id"]
        self.replace(confirmed, UNSURE_IDENTITY)
        with self.assertRaises(ConflictException):
            self.replace(confirmed, RENEWED)
        with self.assertRaises(DocumentNotFoundException):
            self.replace(confirmed, RENEWED, owner=OTHER_OWNER)
        self.assertEqual(len(self.stored_files()), 2)  # nothing stored for the refused ones

    def test_two_renewals_at_the_same_moment_store_only_one(self):
        document_id = self.upload(IDENTITY)["document_id"]
        real = self.service._replaceable
        calls = []

        def another_request_wins(session, owner_id, doc_id):
            document = real(session, owner_id, doc_id)
            calls.append(doc_id)
            if len(calls) == 2:  # inside the transaction: someone else adds version 2 first
                with self.engine.begin() as connection:
                    connection.execute(insert(DocumentVersion).values(document_id=doc_id, version=2,
                                                                      status="processing", review_flags=[],
                                                                      warnings=[], errors=[], events=[]))
            return document

        with patch.object(self.service, "_replaceable", side_effect=another_request_wins), \
                self.assertRaises(ConflictException):
            self.replace(document_id, RENEWED)
        self.assertEqual(len(self.stored_files()), 1)

    def test_the_document_type_stays_the_same(self):
        document_id = self.upload(IDENTITY)["document_id"]
        with self.ocr(RENEWED):
            renewed = self.service.replace_upload(OWNER, document_id, "income_certificate.png", "image/png", self.png)
        self.assertEqual(renewed["document_type"], "identity")

    def test_a_wrong_file_is_refused_before_anything_is_stored(self):
        document_id = self.upload(IDENTITY)["document_id"]
        for mime_type, data in (("text/plain", self.png), ("image/png", b"")):
            with self.subTest(mime_type=mime_type), self.assertRaises(ValidationException):
                self.service.replace_upload(OWNER, document_id, "x.png", mime_type, data)
        self.assertEqual(self.count(DocumentVersion), 1)


class ReplaceManualTests(LifecycleCase):
    def type_in(self, document_id, fields, owner=OWNER):
        with hash_key():
            return self.service.replace_manual(owner, document_id, fields)

    def test_an_uploaded_document_is_replaced_by_typed_in_details(self):
        document_id = self.upload(IDENTITY)["document_id"]
        renewed = self.type_in(document_id, TYPED_IDENTITY)
        self.assertEqual((renewed["version"], renewed["entry"], renewed["file_stored"]), (2, "manual", False))
        self.assertEqual(renewed["fields"]["pan_number"]["value"], "XXXXXX814K")
        self.assertFalse(any("already in the vault" in warning for warning in renewed["warnings"]))
        self.assertEqual(self.versions(document_id), [(1, "replaced", False), (2, "confirmed", False)])
        self.assertEqual(self.stored_files(), [])
        self.assertEqual(self.count(IdentifierHash), 2)  # the typed-in Aadhaar and PAN

    def test_wrong_typed_in_details_leave_the_document_as_it_was(self):
        document_id = self.upload(IDENTITY)["document_id"]
        with self.assertRaises(ValidationException) as caught:
            self.type_in(document_id, {**TYPED_IDENTITY, "date_of_birth": "someday"})
        self.assertEqual(caught.exception.details, {"errors": ["date_of_birth: not valid"]})
        self.assertEqual(self.versions(document_id), [(1, "confirmed", True)])

    def test_typed_in_renewals_need_a_confirmed_document_with_nothing_waiting(self):
        paused = self.upload(UNSURE_IDENTITY)["document_id"]
        with self.assertRaises(ConflictException):
            self.type_in(paused, TYPED_IDENTITY)
        with self.assertRaises(DocumentNotFoundException):
            self.type_in(paused, TYPED_IDENTITY, owner=OTHER_OWNER)

    def test_a_renewal_started_while_typing_is_not_overwritten(self):
        document_id = self.upload(IDENTITY)["document_id"]
        real = self.service._replaceable
        calls = []

        def upload_started_meanwhile(session, owner_id, doc_id):
            calls.append(doc_id)
            if len(calls) == 2:  # between checking the typed values and saving them
                session.execute(text("UPDATE document_versions SET status = 'needs_review'"))
            return real(session, owner_id, doc_id)

        with patch.object(self.service, "_replaceable", side_effect=upload_started_meanwhile), \
                self.assertRaises(ConflictException):
            self.type_in(document_id, TYPED_IDENTITY)
        self.assertEqual(self.count(DocumentVersion), 1)

    def test_a_typed_in_document_can_be_renewed_by_typing_in_again(self):
        with hash_key():
            document_id = self.service.create_manual(OWNER, "identity", TYPED_IDENTITY)["document_id"]
        renewed = self.type_in(document_id, {"name": "Meera Rao", "date_of_birth": "1950-09-17"})
        self.assertEqual(renewed["version"], 2)
        self.assertEqual(set(renewed["fields"]), {"name", "date_of_birth"})
        self.assertEqual(self.count(IdentifierHash), 0)  # the old version's hashes went with it


class MigrationDataTests(DatabaseCase):
    """0003 moves each document's file details to its newest version, and back on downgrade."""

    def alembic(self, action, revision):
        config = Config(str(BACKEND / "alembic.ini"))
        with self.engine.begin() as connection:
            config.attributes["connection"] = connection
            getattr(command, action)(config, revision)

    def test_file_details_move_to_the_newest_version_and_back(self):
        drop_everything(self.engine)
        try:
            self.alembic("upgrade", "0002_manual_entry")
            with self.engine.begin() as connection:
                connection.execute(text(
                    "INSERT INTO documents (id, owner_id, entry, filename, mime_type, size_bytes, sha256, storage_key,"
                    " status) VALUES ('d1', 'o', 'upload', 'card.png', 'image/png', 3, :sha, 'k1', 'confirmed'),"
                    " ('d2', 'o', 'manual', NULL, NULL, NULL, NULL, NULL, 'confirmed')"), {"sha": "a" * 64})
                connection.execute(text(
                    "INSERT INTO document_versions (document_id, version, status, review_flags, warnings, errors,"
                    " events) VALUES ('d1', 1, 'confirmed', '[]', '[]', '[]', '[]'),"
                    " ('d2', 1, 'confirmed', '[]', '[]', '[]', '[]')"))
            self.alembic("upgrade", "head")
            with self.engine.connect() as connection:
                rows = connection.execute(text(
                    "SELECT document_id, entry, filename, size_bytes, storage_key FROM document_versions"
                    " ORDER BY document_id")).all()
            self.assertEqual([tuple(row) for row in rows],
                             [("d1", "upload", "card.png", 3, "k1"), ("d2", "manual", None, None, None)])
            self.alembic("downgrade", "0002_manual_entry")
            with self.engine.connect() as connection:
                rows = connection.execute(text(
                    "SELECT id, entry, filename, storage_key FROM documents ORDER BY id")).all()
            self.assertEqual([tuple(row) for row in rows], [("d1", "upload", "card.png", "k1"),
                                                            ("d2", "manual", None, None)])
        finally:
            drop_everything(self.engine)
            migrate(self.engine)

    def test_downgrade_is_refused_while_a_renewal_has_a_second_file(self):
        with self.engine.begin() as connection:
            connection.execute(text("INSERT INTO documents (id, owner_id, status) VALUES ('d1', 'o', 'confirmed')"))
            connection.execute(text(
                "INSERT INTO document_versions (document_id, version, status, storage_key, review_flags, warnings,"
                " errors, events) VALUES ('d1', 1, 'confirmed', 'k1', '[]', '[]', '[]', '[]'),"
                " ('d1', 2, 'needs_review', 'k2', '[]', '[]', '[]', '[]')"))
        with self.assertRaises(RuntimeError):
            self.alembic("downgrade", "0002_manual_entry")
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(text("SELECT count(*) FROM document_versions")).scalar_one(), 2)


class RouteTests(ApiCase):
    def setUp(self):
        super().setUp()
        self.service = FakeService()
        self.service.delete = lambda *args: self.service.calls.append(("delete", *args))
        self.service.replace_upload = lambda *args, **kwargs: self.service.calls.append(
            ("replace_upload", args, kwargs)) or {**DOCUMENT, "version": 2}
        self.service.replace_manual = lambda *args, **kwargs: self.service.calls.append(
            ("replace_manual", args, kwargs)) or {**DOCUMENT, "version": 2, "entry": "manual"}
        self.app.dependency_overrides[documents.get_document_service] = lambda: self.service
        self.headers = {"X-Actor-Id": OWNER}

    def test_delete(self):
        response = self.client.delete("/api/v1/documents/d1", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"], {"documentId": "d1", "deleted": True})
        self.assertEqual(self.service.calls[-1], ("delete", OWNER, "d1"))
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(self.client.delete("/api/v1/documents/d1").status_code, 401)

    def test_replace_by_upload_passes_the_raw_body_and_options(self):
        response = self.client.post("/api/v1/documents/d1/versions?filename=new.png&medicalCloudConsent=true",
                                    content=b"\x89PNG", headers={**self.headers, "Content-Type": "image/png"})
        self.assertEqual(response.status_code, 201)
        _, args, kwargs = self.service.calls[-1]
        self.assertEqual(args, (OWNER, "d1", "new.png", "image/png", b"\x89PNG"))
        self.assertTrue(kwargs["medical_cloud_consent"])
        self.assertFalse(kwargs["cloud_ocr_consent"])
        self.assertEqual(response.json()["data"]["version"], 2)
        self.assertEqual(self.client.post("/api/v1/documents/d1/versions", content=b"x",
                                          headers={**self.headers, "Content-Type": "image/png"}).status_code, 422)

    def test_replace_by_typing_in(self):
        response = self.client.post("/api/v1/documents/d1/versions/manual", headers=self.headers,
                                    json={"fields": {"name": "Meera Rao"}})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.service.calls[-1], ("replace_manual", (OWNER, "d1", {"name": "Meera Rao"}),
                                                  {"owner_name": None}))
        self.assertEqual(response.json()["data"]["entry"], "manual")
        self.assertEqual(self.client.post("/api/v1/documents/d1/versions/manual", headers=self.headers,
                                          json={}).status_code, 422)


class EndToEndTests(LifecycleCase, ApiCase):
    def setUp(self):
        LifecycleCase.setUp(self)
        ApiCase.setUp(self)
        self.app.dependency_overrides[documents.get_document_service] = lambda: self.service

    def test_renew_through_review_then_delete(self):
        document_id = self.upload(IDENTITY)["document_id"]
        headers = {"X-Actor-Id": OWNER}
        with self.ocr(UNSURE_IDENTITY):
            renewed = self.client.post(f"/api/v1/documents/{document_id}/versions?filename=new.png", content=self.png,
                                       headers={**headers, "Content-Type": "image/png"})
        self.assertEqual(renewed.status_code, 201)
        data = renewed.json()["data"]
        self.assertEqual((data["version"], data["replacement"]["status"]), (1, "needs_review"))
        with self.ocr(UNSURE_IDENTITY):
            again = self.client.post(f"/api/v1/documents/{document_id}/versions?filename=new.png", content=self.png,
                                     headers={**headers, "Content-Type": "image/png"})
        self.assertEqual(again.status_code, 409)
        with self.ocr(UNSURE_IDENTITY):
            done = self.client.post(f"/api/v1/documents/{document_id}/review", headers=headers,
                                    json={"action": "confirm"})
        self.assertEqual((done.json()["data"]["version"], done.json()["data"]["replacement"]), (2, None))
        stranger = self.client.delete(f"/api/v1/documents/{document_id}", headers={"X-Actor-Id": OTHER_OWNER})
        self.assertEqual(stranger.status_code, 404)
        deleted = self.client.delete(f"/api/v1/documents/{document_id}", headers=headers)
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(self.client.get(f"/api/v1/documents/{document_id}", headers=headers).status_code, 404)
        self.assertEqual(self.stored_files(), [])


if __name__ == "__main__":
    unittest.main()
