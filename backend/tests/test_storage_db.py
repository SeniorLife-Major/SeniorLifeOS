"""Tests: the document vault on a real PostgreSQL database.

Needs TEST_DATABASE_URL (environment or .env), naming a database whose name ends in
"_test", e.g. postgresql+asyncpg://USER:PASSWORD@localhost:5432/seniorlife_os_test.
These tests drop and re-create the vault tables in that database. Skipped when unset.
"""

import json
import shutil
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import insert, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from backend.errors import DocumentNotFoundException, ValidationException
from backend.errors.exceptions import ConflictException
from backend.storage.db import make_engine, make_sessions
from backend.storage.files import EncryptedFileStore
from backend.storage.models import AuditEvent, Base, Document, IdentifierHash, WorkflowRun, vault_tables_only
from backend.storage.repository import claim_run
from backend.storage.service import DocumentService, WorkflowFailed
from backend.storage.settings import TEST_DATABASE_URL, setting, with_driver
from backend.utils.checkpoints import close_checkpointer, make_postgres_checkpointer
from test_file_safety import make_pdf
from test_graph_ocr import AADHAAR, ocr_page
from test_ocr import UNIT, ensure_fixtures
from test_privacy import FAKE_HASH_KEY, hash_key_env

URL = setting(TEST_DATABASE_URL)
BACKEND = Path(__file__).resolve().parents[1]
CHECKPOINT_KEY = bytes(range(32))
OTHER_KEY = bytes(range(1, 33))
FILE_KEY = bytes(range(2, 34))
OWNER, OTHER_OWNER = "senior-meera", "senior-ravi"
PAN = "HAVPC1814K"
IDENTITY = [("Name: Meera Rao", 0.99), ("Date of birth: 1950-09-17", 0.99), (f"Aadhaar: {AADHAAR}", 0.99), (PAN, 0.99)]
UNSURE_IDENTITY = [("Name: Meera Rao", 0.6), *IDENTITY[1:]]
CERTIFICATE = [("Name: Meera Rao", 0.99), ("Issuing authority: Medical Board, Demo District Hospital", 0.99),
               ("Issue date: 10/11/2025", 0.99), ("Disability: 40%", 0.99)]
VAULT_TABLES = [table.name for table in reversed(Base.metadata.sorted_tables)]


def drop_everything(engine):
    with engine.begin() as connection:
        checkpoint_tables = connection.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = current_schema() AND tablename LIKE 'checkpoint%'"))
        for table in [*VAULT_TABLES, "alembic_version", *[row[0] for row in checkpoint_tables]]:
            connection.execute(text(f'DROP TABLE IF EXISTS "{table}" CASCADE'))


def migrate(engine):
    config = Config(str(BACKEND / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")


@unittest.skipUnless(URL, "TEST_DATABASE_URL is not set")
class DatabaseCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database = make_url(URL).database or ""
        if not database.endswith("_test"):
            raise RuntimeError("TEST_DATABASE_URL must name a database ending in _test (these tests empty it)")
        ensure_fixtures()
        cls.engine = make_engine(URL)
        drop_everything(cls.engine)
        migrate(cls.engine)
        cls.root = Path(tempfile.mkdtemp())
        cls.files = EncryptedFileStore(cls.root, FILE_KEY)
        cls.checkpointer = make_postgres_checkpointer(with_driver(URL, ""), key=CHECKPOINT_KEY)
        cls.service = DocumentService(make_sessions(cls.engine), cls.files, cls.checkpointer)
        cls.png = (UNIT / "sharp.png").read_bytes()

    @classmethod
    def tearDownClass(cls):
        close_checkpointer(cls.checkpointer)
        cls.engine.dispose()
        shutil.rmtree(cls.root, ignore_errors=True)

    def setUp(self):
        for child in self.root.iterdir():  # files left by the previous test
            shutil.rmtree(child)
        with self.engine.begin() as connection:
            tables = connection.execute(text(
                "SELECT tablename FROM pg_tables WHERE schemaname = current_schema() AND tablename LIKE 'checkpoint%'"
                " AND tablename <> 'checkpoint_migrations'"))
            names = ", ".join(f'"{name}"' for name in [*VAULT_TABLES, *[row[0] for row in tables]])
            connection.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))

    @contextmanager
    def ocr(self, lines):
        with patch("backend.utils.privacy.find_env_file", return_value=None), hash_key_env(FAKE_HASH_KEY), \
                patch("backend.graphs.document_graph.read_document", return_value=[ocr_page(lines)]) as reader:
            yield reader

    def upload(self, lines, claimed_type="identity", owner=OWNER, service=None, **extra):
        with self.ocr(lines):
            return (service or self.service).upload(owner, "card.png", "image/png", self.png,
                                                    claimed_type=claimed_type, **extra)

    def review(self, document_id, action="confirm", edits=None, owner=OWNER, service=None):
        with patch("backend.utils.privacy.find_env_file", return_value=None), hash_key_env(FAKE_HASH_KEY):
            return (service or self.service).review(owner, document_id, action, edits)

    def database_text(self):
        with self.engine.connect() as connection:
            return "\n".join(json.dumps([*row], default=str) for table in Base.metadata.sorted_tables
                             for row in connection.execute(table.select()))

    def checkpoint_rows(self, run_id=None):
        """Every value in LangGraph's checkpoint tables (for this run), as bytes."""
        with self.engine.connect() as connection:
            tables = [row[0] for row in connection.execute(text(
                "SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"
                " AND tablename LIKE 'checkpoint%' AND tablename <> 'checkpoint_migrations'"))]
            rows = []
            for table in tables:
                query = f'SELECT * FROM "{table}"' + (" WHERE thread_id = :run" if run_id else "")
                rows += connection.execute(text(query), {"run": run_id}).all()
        return rows

    @staticmethod
    def as_bytes(rows):
        out = bytearray()
        for row in rows:
            for value in row:
                if isinstance(value, (bytes, memoryview)):
                    out += bytes(value)
                elif value is not None:
                    out += json.dumps(value, default=str).encode()
        return bytes(out)

    def run_id(self, document_id):
        with self.engine.connect() as connection:
            return connection.execute(select(WorkflowRun.id).where(WorkflowRun.document_id == document_id)).scalar_one()

    def assert_no_full_numbers(self, haystack):
        for number in (AADHAAR, AADHAAR.replace(" ", ""), PAN):
            self.assertNotIn(number, haystack)


class MigrationTests(DatabaseCase):
    def test_the_migration_builds_exactly_the_models(self):
        with self.engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={"include_name": vault_tables_only})
            differences = compare_metadata(context, Base.metadata)
        self.assertEqual(differences, [])


class EngineTests(DatabaseCase):
    def test_database_errors_never_show_the_values_sent(self):
        row = {"id": "same-id", "owner_id": OWNER, "filename": "Meera-secret-name.png", "mime_type": "image/png",
               "size_bytes": 1, "sha256": "0" * 64, "status": "processing"}
        with self.engine.begin() as connection:
            connection.execute(insert(Document).values(**row))
        with self.assertRaises(IntegrityError) as caught, self.engine.begin() as connection:
            connection.execute(insert(Document).values(**row))
        self.assertNotIn("Meera-secret-name", str(caught.exception))


class UploadTests(DatabaseCase):
    def test_a_sure_upload_is_confirmed_and_stored_masked(self):
        result = self.upload(IDENTITY)
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["document_type"], "identity")
        self.assertEqual(result["fields"]["aadhaar_number"]["value"], "XXXX XXXX 7973")
        self.assertEqual(result["fields"]["name"]["reviewed_by"], "system:confidence_gate")
        self.assertTrue(result["file_stored"])
        stored = self.database_text()
        self.assert_no_full_numbers(stored)
        self.assertIn("XXXX XXXX 7973", stored)  # masked page text is kept
        self.assertIn("XXXXXX814K", stored)  # so is the unlabelled PAN, masked
        with self.engine.connect() as connection:
            hashes = connection.execute(select(IdentifierHash.field, IdentifierHash.owner_id)).all()
            actions = [row[0] for row in connection.execute(select(AuditEvent.action).order_by(AuditEvent.id))]
        self.assertEqual(hashes, [("aadhaar_number", OWNER)])
        self.assertEqual(actions, ["document.upload", "document.confirmed"])
        self.assertEqual(self.checkpoint_rows(self.run_id(result["document_id"])), [])  # forgotten once ended

    def test_the_stored_file_is_encrypted_and_readable_back(self):
        result = self.upload(IDENTITY)
        with self.engine.connect() as connection:
            key = connection.execute(text("SELECT storage_key FROM documents WHERE id = :id"),
                                     {"id": result["document_id"]}).scalar_one()
        on_disk = (self.root / key[:2] / key).read_bytes()
        self.assertNotIn(self.png[:64], on_disk)
        self.assertEqual(self.files.read(key), self.png)

    def test_wrong_input_stores_nothing(self):
        for kwargs in ({"mime_type": "text/plain"}, {"data": b""}, {"claimed_type": "passport"}):
            args = {"mime_type": "image/png", "data": self.png, "claimed_type": "identity", **kwargs}
            with self.subTest(**{k: str(v)[:20] for k, v in kwargs.items()}), self.assertRaises(ValidationException):
                self.service.upload(OWNER, "x.png", args["mime_type"], args["data"], claimed_type=args["claimed_type"])
        self.assertEqual(self.database_text(), "")
        self.assertEqual(list(self.root.rglob("*")), [])

    def test_a_risky_pdf_is_rejected_and_its_file_removed(self):
        risky = make_pdf(catalog_extra=b"/OpenAction << /S /JavaScript /JS (app.alert(1)) >> ")
        with self.ocr(IDENTITY) as reader:
            result = self.service.upload(OWNER, "form.pdf", "application/pdf", risky)
        self.assertEqual(result["status"], "rejected")
        self.assertFalse(result["file_stored"])
        reader.assert_not_called()
        self.assertEqual([p for p in self.root.rglob("*") if p.is_file()], [])
        self.assertEqual(self.checkpoint_rows(), [])

    def test_a_crash_marks_the_upload_failed_without_its_message(self):
        with self.ocr(IDENTITY), patch.object(self.service.graph, "invoke",
                                              side_effect=RuntimeError(f"broke on {AADHAAR}")):
            with self.assertRaises(WorkflowFailed) as caught:
                self.service.upload(OWNER, "card.png", "image/png", self.png, claimed_type="identity")
        self.assertNotIn(AADHAAR, str(caught.exception))
        documents = self.service.list(OWNER)
        self.assertEqual([d["status"] for d in documents], ["failed_tool"])
        self.assertFalse(documents[0]["file_stored"])
        self.assertNotIn(AADHAAR, self.database_text())
        self.assertIn('"error": "RuntimeError"', self.database_text().replace("'", '"'))
        self.assertEqual([p for p in self.root.rglob("*") if p.is_file()], [])


class ReviewTests(DatabaseCase):
    def test_a_paused_review_keeps_full_numbers_only_in_the_encrypted_checkpoint(self):
        result = self.upload(UNSURE_IDENTITY)
        self.assertEqual(result["status"], "needs_review")
        self.assertIn("name: low confidence (0.60)", result["review_flags"])
        self.assertEqual(result["fields"], {})  # nothing is stored before the owner confirms
        self.assert_no_full_numbers(self.database_text())
        rows = self.checkpoint_rows(self.run_id(result["document_id"]))
        self.assertTrue(rows)
        raw = self.as_bytes(rows)
        for secret in (AADHAAR, AADHAAR.replace(" ", ""), PAN, "Meera"):
            self.assertNotIn(secret.encode(), raw)

    def test_the_owner_sees_full_values_and_confirms(self):
        document_id = self.upload(UNSURE_IDENTITY)["document_id"]
        pending = self.service.pending_review(OWNER, document_id)
        self.assertEqual(pending["candidates"]["aadhaar_number"], AADHAAR.replace(" ", ""))  # as normalized
        self.assertIn("name: low confidence (0.60)", pending["flags"])
        result = self.review(document_id, edits={"name": "Meera Rao"})
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["fields"]["aadhaar_number"]["value"], "XXXX XXXX 7973")
        self.assertTrue(result["fields"]["name"]["corrected_by_user"])
        self.assertEqual(result["fields"]["name"]["reviewed_by"], OWNER)
        self.assert_no_full_numbers(self.database_text())
        self.assertEqual(self.checkpoint_rows(self.run_id(document_id)), [])

    def test_someone_else_cannot_see_or_review(self):
        document_id = self.upload(UNSURE_IDENTITY)["document_id"]
        for call in (lambda: self.service.pending_review(OTHER_OWNER, document_id),
                     lambda: self.review(document_id, owner=OTHER_OWNER),
                     lambda: self.service.get(OTHER_OWNER, document_id)):
            with self.assertRaises(DocumentNotFoundException):
                call()
        self.assertEqual(self.service.get(OWNER, document_id)["status"], "needs_review")

    def test_rejecting_removes_the_file_and_the_checkpoint(self):
        document_id = self.upload(UNSURE_IDENTITY)["document_id"]
        result = self.review(document_id, action="reject")
        self.assertEqual(result["status"], "rejected")
        self.assertFalse(result["file_stored"])
        self.assertEqual([p for p in self.root.rglob("*") if p.is_file()], [])
        self.assertEqual(self.checkpoint_rows(self.run_id(document_id)), [])

    def test_a_refused_decision_can_be_corrected_without_reading_again(self):
        with self.ocr(UNSURE_IDENTITY) as reader:
            document_id = self.service.upload(OWNER, "card.png", "image/png", self.png,
                                              claimed_type="identity")["document_id"]
            first = self.service.review(OWNER, document_id, "confirm", {"name": ""})
            self.assertEqual(first["status"], "needs_review")
            self.assertEqual(first["errors"], ["Correct these required fields: name"])
            self.assertEqual(self.service.pending_review(OWNER, document_id)["errors"],
                             ["Correct these required fields: name"])
            second = self.service.review(OWNER, document_id, "confirm", {"name": "Meera Rao"})
        self.assertEqual(second["status"], "confirmed")
        self.assertEqual(reader.call_count, 1)

    def test_a_finished_review_cannot_be_applied_again(self):
        document_id = self.upload(UNSURE_IDENTITY)["document_id"]
        self.review(document_id)
        with self.assertRaises(ConflictException):
            self.review(document_id, action="reject")

    def test_a_document_not_waiting_has_nothing_to_review(self):
        document_id = self.upload(IDENTITY)["document_id"]  # confirmed straight away
        with self.assertRaisesRegex(ConflictException, "not waiting for review"):
            self.service.pending_review(OWNER, document_id)

    def test_only_one_request_can_take_a_paused_run(self):
        document_id = self.upload(UNSURE_IDENTITY)["document_id"]
        run_id = self.run_id(document_id)
        sessions = make_sessions(self.engine)
        with sessions.begin() as session:
            self.assertTrue(claim_run(session, run_id, ("paused",)))
        with sessions.begin() as session:
            self.assertFalse(claim_run(session, run_id, ("paused",)))
        with self.assertRaises(ConflictException):
            self.review(document_id)

    def test_a_paused_review_survives_a_restart_but_not_another_key(self):
        document_id = self.upload(UNSURE_IDENTITY)["document_id"]
        wrong = make_postgres_checkpointer(with_driver(URL, ""), key=OTHER_KEY)
        self.addCleanup(close_checkpointer, wrong)
        with self.assertRaises(WorkflowFailed):
            self.review(document_id, service=DocumentService(make_sessions(self.engine), self.files, wrong))
        restarted = make_postgres_checkpointer(with_driver(URL, ""), key=CHECKPOINT_KEY)
        self.addCleanup(close_checkpointer, restarted)
        service = DocumentService(make_sessions(self.engine), self.files, restarted)
        self.assertEqual(self.review(document_id, service=service)["status"], "confirmed")


class VaultTests(DatabaseCase):
    def test_the_same_card_again_is_noticed_only_for_the_same_owner(self):
        first = self.upload(IDENTITY)
        second = self.upload(IDENTITY)
        self.assertIn(f"aadhaar_number already in the vault (document {first['document_id']})", second["warnings"])
        someone_else = self.upload(IDENTITY, owner=OTHER_OWNER)
        self.assertFalse(any("already in the vault" in warning for warning in someone_else["warnings"]))

    def test_a_name_slightly_different_from_an_earlier_document_is_flagged(self):
        first = self.upload(CERTIFICATE, claimed_type="disability_certificate")
        self.assertEqual(first["status"], "confirmed")
        changed = [CERTIFICATE[0], ("Issuing authority: Medical Board, Demo Distrlct Hospital", 0.99), *CERTIFICATE[2:]]
        second = self.upload(changed, claimed_type="disability_certificate")
        self.assertEqual(second["status"], "needs_review")
        self.assertIn("issuing_authority: differs slightly from your earlier document, please check",
                      second["review_flags"])
        other = self.upload(changed, claimed_type="disability_certificate", owner=OTHER_OWNER)
        self.assertNotIn("issuing_authority: differs slightly from your earlier document, please check",
                         other["review_flags"])

    def test_list_and_get_only_show_my_documents(self):
        identity = self.upload(IDENTITY)["document_id"]
        certificate = self.upload(CERTIFICATE, claimed_type="disability_certificate")["document_id"]
        self.upload(IDENTITY, owner=OTHER_OWNER)
        self.assertEqual({d["document_id"] for d in self.service.list(OWNER)}, {identity, certificate})
        self.assertEqual([d["document_id"] for d in self.service.list(OWNER, "identity")], [identity])
        self.assertEqual(self.service.get(OWNER, identity)["fields"]["name"]["value"], "Meera Rao")
        self.assertNotIn("fields", self.service.list(OWNER)[0])


if __name__ == "__main__":
    unittest.main()
