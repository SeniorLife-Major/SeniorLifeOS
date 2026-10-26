"""Tests: the document vault API (api/documents.py)."""

import asyncio
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from backend.api import documents
from backend.errors import register_global_error_handlers
from backend.graphs.document_graph import MAX_UPLOAD_BYTES
from test_graph_ocr import AADHAAR
from test_storage_db import IDENTITY, OTHER_OWNER, OWNER, UNSURE_IDENTITY, DatabaseCase

DOCUMENT = {"document_id": "d1", "filename": "card.png", "mime_type": "image/png", "size_bytes": 3,
            "document_type": "identity", "status": "confirmed", "version": 1, "file_stored": True,
            "review_flags": [], "warnings": [], "errors": [],
            "fields": {"aadhaar_number": {"value": "XXXX XXXX 7973", "source_ref": "document:d1:page:1:line:2",
                                          "reviewed_by": OWNER, "corrected_by_user": False}}}


def config(environment="development"):
    return SimpleNamespace(app=SimpleNamespace(environment=environment, version="test"),
                           database=SimpleNamespace(pool_size=3, max_overflow=4, echo=True))


class FakeService:
    def __init__(self):
        self.calls = []

    def upload(self, *args, **kwargs):
        self.calls.append(("upload", args, kwargs))
        return DOCUMENT

    def list(self, owner, document_type=None):
        self.calls.append(("list", owner, document_type))
        return [{k: v for k, v in DOCUMENT.items() if k not in ("review_flags", "warnings", "errors", "fields")}]

    def review(self, owner, document_id, action, edits):
        self.calls.append(("review", owner, document_id, action, edits))
        return DOCUMENT


class ApiCase(unittest.TestCase):
    environment = "development"

    def setUp(self):
        for target in ("backend.api.documents.get_config", "backend.errors.global_error_handler.get_config"):
            patcher = patch(target, return_value=config(self.environment))
            patcher.start()
            self.addCleanup(patcher.stop)
        app = FastAPI()
        register_global_error_handlers(app)
        app.include_router(documents.router)
        self.app = app
        self.client = TestClient(app, raise_server_exceptions=False)


class ActorTests(ApiCase):
    def test_no_or_bad_actor_header(self):
        self.app.dependency_overrides[documents.get_document_service] = FakeService
        for headers in ({}, {"X-Actor-Id": "two words"}, {"X-Actor-Id": "x" * 129}):
            with self.subTest(headers=headers):
                response = self.client.get("/api/v1/documents", headers=headers)
                self.assertEqual(response.status_code, 401)


class ProductionTests(ApiCase):
    environment = "production"

    def test_the_dev_header_is_refused_in_production(self):
        self.app.dependency_overrides[documents.get_document_service] = FakeService
        response = self.client.get("/api/v1/documents", headers={"X-Actor-Id": OWNER})
        self.assertEqual(response.status_code, 401)
        self.assertIn("production", response.json()["error"]["message"])


class StorageSetupTests(ApiCase):
    def test_missing_settings_give_503_naming_the_setting(self):
        clean = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "SENIORLIFE_STORAGE_KEY")}
        with patch.dict(os.environ, clean, clear=True), \
                patch("backend.storage.settings.find_env_file", return_value=None), \
                patch.object(documents, "_service", None):
            response = self.client.get("/api/v1/documents", headers={"X-Actor-Id": OWNER})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "STORAGE_UNAVAILABLE")
        self.assertIn("DATABASE_URL is not set", response.json()["error"]["message"])

    def test_pool_and_sql_logging_come_from_application_yml(self):
        settings = {"DATABASE_URL": "postgresql+asyncpg://user:pw-secret@localhost:1/x_db",
                    "SENIORLIFE_STORAGE_KEY": "ab" * 32}
        made = {}

        def fake_engine(url, **kwargs):
            made.update(kwargs)
            raise ConnectionError("no database here")

        with patch.dict(os.environ, settings), patch("backend.storage.settings.find_env_file", return_value=None), \
                patch("backend.storage.db.make_engine", side_effect=fake_engine), \
                patch.object(documents, "_service", None):
            response = self.client.get("/api/v1/documents", headers={"X-Actor-Id": OWNER})
        self.assertEqual(made, {"pool_size": 3, "max_overflow": 4, "echo": True})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("pw-secret", response.text)


class ApplicationYmlTests(unittest.TestCase):
    def test_database_section_has_no_built_in_password_and_logs_no_sql(self):
        from backend.core.config import load_config

        clean = {k: v for k, v in os.environ.items() if not k.startswith("DATABASE_")}
        with patch.dict(os.environ, clean, clear=True), patch("backend.utils.env.find_env_file", return_value=None):
            database = load_config(Path(documents.__file__).resolve().parents[1] / "config" / "application.yml").database
        self.assertEqual(database.url, "")
        self.assertFalse(database.echo)
        self.assertEqual((database.pool_size, database.max_overflow), (5, 10))


class EngineTests(unittest.TestCase):
    def test_engine_settings_and_hidden_values(self):
        from backend.storage.db import make_engine

        engine = make_engine("postgresql+asyncpg://user:pw@localhost:1/x", pool_size=3, max_overflow=4, echo=True)
        self.addCleanup(engine.dispose)
        self.assertEqual(engine.url.drivername, "postgresql+psycopg")
        self.assertEqual((engine.pool.size(), engine.pool._max_overflow), (3, 4))
        self.assertTrue(engine.echo)
        self.assertTrue(engine.hide_parameters)


class RouteTests(ApiCase):
    def setUp(self):
        super().setUp()
        self.service = FakeService()
        self.app.dependency_overrides[documents.get_document_service] = lambda: self.service
        self.headers = {"X-Actor-Id": OWNER}

    def test_upload_passes_the_raw_body_type_and_options(self):
        response = self.client.post(
            "/api/v1/documents?filename=card.png&claimedType=identity&cloudOcrConsent=true",
            content=b"\x89PNG", headers={**self.headers, "Content-Type": "image/png; charset=binary"})
        self.assertEqual(response.status_code, 201)
        _, args, kwargs = self.service.calls[0]
        self.assertEqual(args, (OWNER, "card.png", "image/png", b"\x89PNG"))
        self.assertEqual(kwargs["claimed_type"], "identity")
        self.assertTrue(kwargs["cloud_ocr_consent"])
        self.assertFalse(kwargs["medical_cloud_consent"])
        body = response.json()
        self.assertEqual(body["data"]["documentId"], "d1")
        self.assertTrue(body["data"]["fileStored"])
        self.assertEqual(body["data"]["fields"]["aadhaar_number"]["value"], "XXXX XXXX 7973")
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_upload_needs_a_filename(self):
        response = self.client.post("/api/v1/documents", content=b"x",
                                    headers={**self.headers, "Content-Type": "image/png"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.service.calls, [])

    def test_too_large_is_refused_before_reading(self):
        response = self.client.post("/api/v1/documents?filename=big.png", content=b"x" * (MAX_UPLOAD_BYTES + 1),
                                    headers={**self.headers, "Content-Type": "image/png"})
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.service.calls, [])

    def test_a_body_without_a_length_is_cut_off_at_the_limit(self):
        chunks = [b"x" * 1_000_000] * 6
        received = []

        async def receive():
            body = chunks[len(received)] if len(received) < len(chunks) else b""
            received.append(body)
            return {"type": "http.request", "body": body, "more_body": len(received) < len(chunks)}

        request = Request({"type": "http", "method": "POST", "headers": []}, receive)
        with self.assertRaises(documents.PayloadTooLarge):
            asyncio.run(documents.upload_body(request))
        self.assertEqual(len(received), 6)  # stopped as soon as it passed 5 MB

    def test_list_with_type_and_review(self):
        response = self.client.get("/api/v1/documents?type=identity", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.service.calls[-1], ("list", OWNER, "identity"))
        response = self.client.post("/api/v1/documents/d1/review", headers=self.headers,
                                    json={"action": "confirm", "edits": {"name": "Meera Rao"}})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.service.calls[-1], ("review", OWNER, "d1", "confirm", {"name": "Meera Rao"}))
        response = self.client.post("/api/v1/documents/d1/review", headers=self.headers, json={"action": "approve"})
        self.assertEqual(response.status_code, 422)


class EndToEndTests(DatabaseCase, ApiCase):
    """Upload, review and read through HTTP, on the test database."""

    def setUp(self):
        DatabaseCase.setUp(self)
        ApiCase.setUp(self)
        self.app.dependency_overrides[documents.get_document_service] = lambda: self.service

    def post_upload(self, lines, owner=OWNER):
        with self.ocr(lines):
            return self.client.post("/api/v1/documents?filename=card.png&claimedType=identity", content=self.png,
                                    headers={"X-Actor-Id": owner, "Content-Type": "image/png"})

    def test_upload_review_and_read(self):
        uploaded = self.post_upload(UNSURE_IDENTITY)
        self.assertEqual(uploaded.status_code, 201)
        document_id = uploaded.json()["data"]["documentId"]
        self.assertEqual(uploaded.json()["data"]["status"], "needs_review")
        self.assertNotIn(AADHAAR.replace(" ", ""), uploaded.text)

        pending = self.client.get(f"/api/v1/documents/{document_id}/review", headers={"X-Actor-Id": OWNER})
        self.assertEqual(pending.json()["data"]["candidates"]["aadhaar_number"], AADHAAR.replace(" ", ""))
        self.assertEqual(pending.headers["cache-control"], "no-store")
        stranger = self.client.get(f"/api/v1/documents/{document_id}/review", headers={"X-Actor-Id": OTHER_OWNER})
        self.assertEqual(stranger.status_code, 404)
        self.assertNotIn(AADHAAR.replace(" ", ""), stranger.text)

        with self.ocr(UNSURE_IDENTITY):
            confirmed = self.client.post(f"/api/v1/documents/{document_id}/review", headers={"X-Actor-Id": OWNER},
                                         json={"action": "confirm"})
        self.assertEqual(confirmed.json()["data"]["status"], "confirmed")
        read = self.client.get(f"/api/v1/documents/{document_id}", headers={"X-Actor-Id": OWNER})
        self.assertEqual(read.json()["data"]["fields"]["aadhaar_number"]["value"], "XXXX XXXX 7973")
        again = self.client.post(f"/api/v1/documents/{document_id}/review", headers={"X-Actor-Id": OWNER},
                                 json={"action": "reject"})
        self.assertEqual(again.status_code, 409)

    def test_listing_is_per_owner(self):
        self.post_upload(IDENTITY)
        self.post_upload(IDENTITY, owner=OTHER_OWNER)
        mine = self.client.get("/api/v1/documents", headers={"X-Actor-Id": OWNER}).json()["data"]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]["status"], "confirmed")


if __name__ == "__main__":
    unittest.main()
