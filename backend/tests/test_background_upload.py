"""Background upload (fastapi-backend Phase 3): an upload read by a worker thread, the limit per
server, and runs left "queued" or "running" by a server that stopped.

Real PostgreSQL (TEST_DATABASE_URL), real encrypted files and checkpoints; OCR is faked and
can be held at a gate, so the order of events is known.
"""

from __future__ import annotations

import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import func, select, text, update

from backend.errors.exceptions import ConflictException
from backend.storage.db import make_sessions
from backend.storage.models import AuditEvent, Document, DocumentVersion, WorkflowRun
from backend.storage.repository import claim_run
from backend.storage.service import (
    BACKGROUND_SLOTS,
    BACKGROUND_WORKERS,
    INTERRUPTED,
    STUCK_MINUTES,
    TIDY_EVERY_SECONDS,
    DocumentService,
    ReaderBusy,
    WorkflowFailed,
)
from test_graph_ocr import ocr_page
from test_privacy import FAKE_HASH_KEY, hash_key_env
from test_storage_db import IDENTITY, OWNER, UNSURE_IDENTITY, DatabaseCase

DROPPED = SimpleNamespace(submit=lambda *args, **kwargs: None,  # a server that stops before reading
                          shutdown=lambda **kwargs: None)


def wait_until(check, seconds=10.0):
    deadline = time.monotonic() + seconds
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        time.sleep(0.02)


class BackgroundCase(DatabaseCase):
    def setUp(self):
        super().setUp()
        self.reader = DocumentService(make_sessions(self.engine), self.files, self.checkpointer)
        self.addCleanup(lambda: self.reader.background.shutdown(wait=True, cancel_futures=True))
        self.gate = threading.Event()
        self.addCleanup(self.gate.set)  # runs first: no reader is left waiting

    @contextmanager
    def reading(self, lines, gate=None, fail=False, during=None):
        """Fake OCR for every thread: waits at the gate, runs `during`, then reads `lines` (or fails)."""
        def read(*args, **kwargs):
            if gate is not None:
                gate.wait(10)
            if during is not None:
                during()
            if fail:
                raise RuntimeError("the reader broke")
            return [ocr_page(lines)]

        with patch("backend.utils.privacy.find_env_file", return_value=None), hash_key_env(FAKE_HASH_KEY), \
                patch("backend.graphs.document_graph.read_document", side_effect=read) as reader:
            yield reader

    def send(self, owner=OWNER, document_id=None):
        if document_id is not None:
            return self.reader.replace_upload(owner, document_id, "new.png", "image/png", self.png, background=True)
        return self.reader.upload(owner, "card.png", "image/png", self.png, claimed_type="identity", background=True)

    def finish(self):
        """Wait for every reading handed over, then take new ones again (a fresh pool)."""
        self.reader.background.shutdown(wait=True)
        self.reader.background = ThreadPoolExecutor(BACKGROUND_WORKERS)

    def runs(self, status=None):
        with self.engine.connect() as connection:
            query = select(WorkflowRun.status).order_by(WorkflowRun.started_at)
            return [row[0] for row in connection.execute(query)
                    if status is None or row[0] == status]

    def run_of(self, document_id, version=-1):
        with self.engine.connect() as connection:
            return connection.execute(select(WorkflowRun).where(WorkflowRun.document_id == document_id)
                                      .order_by(WorkflowRun.started_at)).all()[version]

    def stored_files(self):
        return sorted(path.name for path in self.root.rglob("*") if path.is_file())

    def age(self, run_id, minutes=STUCK_MINUTES + 1, status=None):
        """As if nothing had happened to the run for `minutes`."""
        with self.engine.begin() as connection:
            values = {"updated_at": func.now() - text(f"interval '{minutes} minutes'")}
            connection.execute(update(WorkflowRun).where(WorkflowRun.id == run_id)
                               .values(**values, **({"status": status} if status else {})))

    def audits(self, action):
        with self.engine.connect() as connection:
            return [row[0] for row in connection.execute(select(AuditEvent.detail)
                                                         .where(AuditEvent.action == action))]


class BackgroundUploadTests(BackgroundCase):
    def test_returns_at_once_then_reads_like_a_normal_upload(self):
        with self.reading(UNSURE_IDENTITY, self.gate):
            started = self.send()
            self.assertEqual(started["status"], "processing")
            self.assertEqual(self.reader.get(OWNER, started["document_id"])["status"], "processing")
            self.gate.set()
            self.finish()
        done = self.reader.get(OWNER, started["document_id"])
        normal = self.upload(UNSURE_IDENTITY)
        for key in ("status", "document_type", "review_flags", "file_stored"):
            self.assertEqual(done[key], normal[key], key)
        self.assertEqual(done["status"], "needs_review")
        self.assertEqual(self.review(started["document_id"], service=self.reader)["status"], "confirmed")

    def test_the_answer_is_processing_even_when_reading_ends_at_once(self):
        class AtOnce:  # a reader so quick it finishes before the upload call returns
            def submit(self, job, *args):
                job(*args)

            def shutdown(self, **kwargs):
                pass

        self.reader.background = AtOnce()
        with self.reading(UNSURE_IDENTITY):
            started = self.send()
        self.assertEqual(started["status"], "processing")
        self.assertEqual(self.reader.get(OWNER, started["document_id"])["status"], "needs_review")

    def test_a_server_shutting_down_refuses_cleanly(self):
        self.reader.background.shutdown()
        with self.reading(IDENTITY), self.assertLogs("backend.storage.service", level="ERROR"):
            with self.assertRaises(WorkflowFailed):
                self.send()
        self.assertEqual([item["status"] for item in self.reader.list(OWNER)], ["failed_tool"])
        self.assertEqual(self.stored_files(), [])
        self.finish()
        with self.reading(IDENTITY):  # its place was given back
            for _ in range(BACKGROUND_SLOTS):
                self.send()
            self.finish()

    def test_uploads_wait_their_turn_and_the_rest_are_refused(self):
        with self.reading(IDENTITY, self.gate) as reader:
            ids = [self.send()["document_id"] for _ in range(BACKGROUND_SLOTS)]
            wait_until(lambda: len(self.runs("running")) == BACKGROUND_WORKERS)
            self.assertEqual(sorted(self.runs()), ["queued"] * (BACKGROUND_SLOTS - BACKGROUND_WORKERS)
                             + ["running"] * BACKGROUND_WORKERS)
            files = self.stored_files()
            with self.assertRaises(ReaderBusy) as refused:
                self.send()
            self.assertEqual(refused.exception.status_code, 503)
            self.assertEqual(self.stored_files(), files)  # nothing stored for the refused one
            self.assertEqual(len(self.runs()), BACKGROUND_SLOTS)
            self.gate.set()
            self.finish()
            self.assertEqual(reader.call_count, BACKGROUND_SLOTS)
            self.assertEqual({self.reader.get(OWNER, i)["status"] for i in ids}, {"confirmed"})
            for _ in range(BACKGROUND_SLOTS):  # every place was given back
                self.send()
            self.finish()

    def test_a_failed_reading_is_saved_and_gives_its_place_back(self):
        with self.reading(IDENTITY, fail=True), self.assertLogs("backend.storage.service", level="ERROR") as logs:
            ids = [self.send()["document_id"] for _ in range(BACKGROUND_SLOTS)]
            self.finish()
        self.assertIn("Processing a document failed: RuntimeError", "\n".join(logs.output))
        self.assertNotIn("the reader broke", "\n".join(logs.output))
        for document_id in ids:
            failed = self.reader.get(OWNER, document_id)
            self.assertEqual((failed["status"], failed["file_stored"]), ("failed_tool", False))
        self.assertEqual(self.stored_files(), [])
        self.assertEqual(self.checkpoint_rows(), [])
        with self.reading(IDENTITY):
            for _ in range(BACKGROUND_SLOTS):
                self.send()
            self.finish()

    def test_a_waiting_upload_can_be_deleted_and_is_then_skipped(self):
        with self.reading(IDENTITY, self.gate) as reader:
            ids = [self.send()["document_id"] for _ in range(BACKGROUND_WORKERS + 1)]
            wait_until(lambda: len(self.runs("running")) == BACKGROUND_WORKERS)
            self.reader.delete(OWNER, ids[-1])  # still waiting
            with self.assertRaises(ConflictException):
                self.reader.delete(OWNER, ids[0])  # being read
            self.gate.set()
            self.finish()
        self.assertEqual(reader.call_count, BACKGROUND_WORKERS)
        self.assertEqual(len(self.stored_files()), BACKGROUND_WORKERS)
        self.assertEqual([item["document_id"] for item in self.reader.list(OWNER)].count(ids[-1]), 0)

    def test_a_renewal_in_the_background(self):
        document_id = self.upload(IDENTITY)["document_id"]
        with self.reading(UNSURE_IDENTITY):
            started = self.send(document_id=document_id)
            self.assertEqual((started["status"], started["version"]), ("confirmed", 1))  # the old one stays in use
            self.assertEqual(started["replacement"]["status"], "processing")
            self.finish()
        self.assertEqual(self.reader.get(OWNER, document_id)["replacement"]["status"], "needs_review")


class StoppedServerTests(BackgroundCase):
    """Runs left "queued" or "running" by a server that stopped (fail_stuck_runs)."""

    def test_runs_left_behind_are_tidied(self):
        with self.reading(UNSURE_IDENTITY):
            review_id = self.upload(UNSURE_IDENTITY)["document_id"]  # waiting for review
            renewed_id = self.upload(IDENTITY)["document_id"]
            self.reader.background = DROPPED
            queued_id = self.send()["document_id"]
            running_id = self.send()["document_id"]
            renewal = self.send(document_id=renewed_id)
            fresh_id = self.send()["document_id"]
        with self.engine.begin() as connection:  # a review was being applied when the server stopped
            self.assertTrue(claim_run(connection, self.run_of(review_id).id, ("paused",)))
        self.age(self.run_of(review_id).id)
        self.age(self.run_of(queued_id).id)
        self.age(self.run_of(running_id).id, status="running")
        self.age(self.run_of(renewed_id).id)
        self.assertEqual(renewal["replacement"]["status"], "processing")

        self.assertEqual(self.reader.fail_stuck_runs(), 4)
        for document_id in (queued_id, running_id):
            failed = self.reader.get(OWNER, document_id)
            self.assertEqual((failed["status"], failed["errors"], failed["file_stored"]),
                             ("failed_tool", [INTERRUPTED], False))
            self.assertEqual(self.run_of(document_id).status, "failed")
            self.assertEqual(self.checkpoint_rows(self.run_of(document_id).id), [])
        renewed = self.reader.get(OWNER, renewed_id)
        self.assertEqual((renewed["status"], renewed["version"], renewed["file_stored"]), ("confirmed", 1, True))
        self.assertEqual((renewed["replacement"]["status"], renewed["replacement"]["errors"]),
                         ("failed_tool", [INTERRUPTED]))
        self.assertEqual(self.run_of(review_id).status, "paused")  # the owner can review again
        self.assertNotEqual(self.checkpoint_rows(self.run_of(review_id).id), [])
        self.assertEqual(self.reader.get(OWNER, fresh_id)["status"], "processing")  # not old enough
        self.assertEqual(len(self.audits("document.review_interrupted")), 1)
        self.assertEqual(len([detail for detail in self.audits("document.failed")
                              if detail.get("error") == "Interrupted"]), 3)
        self.assertEqual(len(self.stored_files()), 3)  # review, renewed v1, fresh, and nothing else

        self.assertEqual(self.reader.fail_stuck_runs(), 0)
        self.assertEqual(self.review(review_id, service=self.reader)["status"], "confirmed")
        self.reader.delete(OWNER, running_id)  # was refused "being read right now" for ever

    def test_the_first_document_call_after_a_restart_tidies_then_once_a_minute(self):
        with self.reading(IDENTITY):
            self.reader.background = DROPPED
            first, second = self.send()["document_id"], self.send()["document_id"]
        self.age(self.run_of(first).id)
        restarted = DocumentService(make_sessions(self.engine), self.files, self.checkpointer)
        self.addCleanup(restarted.background.shutdown)
        self.assertEqual(restarted.get(OWNER, first)["status"], "failed_tool")
        self.age(self.run_of(second).id)
        self.assertEqual(restarted.get(OWNER, second)["status"], "processing")  # tidied less than a minute ago
        later = time.monotonic() + TIDY_EVERY_SECONDS + 1
        with patch("backend.storage.service.time.monotonic", return_value=later):
            self.assertEqual(restarted.get(OWNER, second)["status"], "failed_tool")

    def test_a_review_resumed_long_after_the_upload_is_not_stuck(self):
        document_id = self.upload(UNSURE_IDENTITY)["document_id"]
        self.age(self.run_of(document_id).id, minutes=3 * STUCK_MINUTES)  # waited hours for the owner: normal
        real_resume = self.reader._resume

        def resume(run_id, decision):  # another server tidies while this review is applied
            self.assertEqual(self.reader.fail_stuck_runs(), 0)
            return real_resume(run_id, decision)

        with patch.object(self.reader, "_resume", side_effect=resume):
            self.assertEqual(self.review(document_id, service=self.reader)["status"], "confirmed")

    def test_a_reading_given_up_while_it_ran_is_not_saved(self):
        def given_up_meanwhile():  # the reading took so long that another server gave it up
            run_id = self.run_of(document_id).id
            self.age(run_id)
            self.assertEqual(self.reader.fail_stuck_runs(), 1)

        with self.reading(IDENTITY, self.gate, during=given_up_meanwhile), \
                self.assertLogs("backend.storage.service", level="WARNING") as logs:
            document_id = self.send()["document_id"]
            self.gate.set()
            self.finish()
        self.assertIn("its result was not saved", "\n".join(logs.output))
        dropped = self.reader.get(OWNER, document_id)
        self.assertEqual((dropped["status"], dropped["errors"], dropped["fields"]), ("failed_tool", [INTERRUPTED], {}))
        self.assertEqual(self.run_of(document_id).status, "failed")
        self.assertEqual(self.checkpoint_rows(self.run_of(document_id).id), [])
        self.assertEqual(self.stored_files(), [])
        self.assertEqual(self.audits("document.confirmed"), [])


if __name__ == "__main__":
    unittest.main()
