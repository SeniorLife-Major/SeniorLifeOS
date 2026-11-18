"""Request IDs (fastapi-backend Phase 5): the X-Request-ID header, meta.requestId in every
envelope, and the ID on log lines, also from the threads that work for a request."""

from __future__ import annotations

import logging
import re
import threading
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import health_requests
from backend.common.request_id import RequestIdLogFilter, RequestIdMiddleware, current_request_id, in_this_request
from backend.errors import NotFoundException, register_global_error_handlers
from backend.main import app as main_app
from test_background_upload import BackgroundCase
from test_storage_db import IDENTITY

NEW_ID = re.compile(r"[0-9a-f]{32}")


class Capture(logging.Handler):
    """Log records as a server handler sees them (with the request-ID filter)."""

    def __init__(self, logger_name):
        super().__init__()
        self.addFilter(RequestIdLogFilter())
        self.records = []
        self.logger = logging.getLogger(logger_name)

    def emit(self, record):
        self.records.append(record)

    def __enter__(self):
        self.logger.addHandler(self)
        return self

    def __exit__(self, *exc):
        self.logger.removeHandler(self)

    def ids(self, words):
        return [record.request_id for record in self.records if words in record.getMessage()]


def small_app():
    """The same wiring as backend/main.py, with routes that fail in each way."""
    app = FastAPI()
    register_global_error_handlers(app)
    app.add_middleware(RequestIdMiddleware)
    log = logging.getLogger("request-id-test")

    @app.get("/fine")
    def fine():
        log.warning("in the route")
        done = threading.Event()

        def later(name):
            log.warning(name)
            done.set()

        threading.Thread(target=in_this_request(later, "in a worker, carried")).start()
        done.wait(5)
        done.clear()
        threading.Thread(target=later, args=("in a worker, not carried",)).start()
        done.wait(5)
        return {"ok": True}

    @app.get("/missing")
    def missing():
        raise NotFoundException("Not here")

    @app.get("/broken")
    def broken():
        raise RuntimeError("something private 9876543210")

    return app


class HeaderAndEnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main_app, raise_server_exceptions=False)

    def test_every_answer_has_an_id_and_the_envelope_repeats_it(self):
        first = self.client.get("/api/v1/health")
        second = self.client.get("/api/v1/health")
        for response in (first, second):
            self.assertRegex(response.headers["x-request-id"], NEW_ID)
            self.assertEqual(response.json()["meta"]["requestId"], response.headers["x-request-id"])
        self.assertNotEqual(first.headers["x-request-id"], second.headers["x-request-id"])

    def test_errors_carry_it_too(self):
        for response in (self.client.get("/api/v1/documents"),  # 401: no sign-in
                         self.client.get("/api/v1/no-such-page"),  # 404 from the router
                         self.client.post("/api/v1/auth/login", json={})):  # 422
            with self.subTest(status=response.status_code):
                self.assertGreaterEqual(response.status_code, 400)
                self.assertEqual(response.json()["meta"]["requestId"], response.headers["x-request-id"])

    def test_a_plain_id_from_the_caller_is_kept_anything_else_replaced(self):
        kept = self.client.get("/api/v1/health", headers={"X-Request-ID": "app-7f3a9c21.retry_2"})
        self.assertEqual(kept.headers["x-request-id"], "app-7f3a9c21.retry_2")
        self.assertEqual(kept.json()["meta"]["requestId"], "app-7f3a9c21.retry_2")
        for sent in ("short", "a" * 65, "has spaces in it", "line\tbreak-1234", "<script>alert(1)</script>"):
            with self.subTest(sent=sent):
                response = self.client.get("/api/v1/health", headers={"X-Request-ID": sent})
                self.assertRegex(response.headers["x-request-id"], NEW_ID)

    def test_a_browser_preflight_gets_one(self):
        response = self.client.options("/api/v1/health", headers={
            "Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"})
        self.assertEqual(response.status_code, 200)
        self.assertRegex(response.headers["x-request-id"], NEW_ID)


class UnexpectedErrorAndLogTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(small_app(), raise_server_exceptions=False)

    def test_an_unexpected_error_carries_the_id_in_header_body_and_log(self):
        with Capture("backend.errors.global_error_handler") as logs:
            response = self.client.get("/broken", headers={"X-Request-ID": "trace-broken-0001"})
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.headers["x-request-id"], "trace-broken-0001")
        self.assertEqual(response.json()["meta"]["requestId"], "trace-broken-0001")
        self.assertNotIn("9876543210", response.text)
        self.assertEqual(logs.ids("Unhandled internal exception"), ["trace-broken-0001"])

    def test_an_app_error(self):
        response = self.client.get("/missing")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["meta"]["requestId"], response.headers["x-request-id"])

    def test_log_lines_of_a_request_and_of_its_worker_threads(self):
        with Capture("request-id-test") as logs:
            response = self.client.get("/fine", headers={"X-Request-ID": "trace-fine-00001"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(logs.ids("in the route"), ["trace-fine-00001"])
        self.assertEqual(logs.ids("in a worker, carried"), ["trace-fine-00001"])
        self.assertEqual(logs.ids("in a worker, not carried"), ["-"])  # why in_this_request is needed
        with Capture("request-id-test") as outside:
            logging.getLogger("request-id-test").warning("outside a request")
        self.assertEqual(outside.ids("outside a request"), ["-"])


class WorkerThreadTests(unittest.TestCase):
    def test_the_doctor_search_stream_worker_keeps_the_id(self):
        def failing_events():
            raise RuntimeError("the graph broke")
            yield  # a generator

        token = current_request_id.set("trace-stream-001")
        try:
            with Capture("backend.api.health_requests") as logs:
                events = list(health_requests.stream(failing_events()))
        finally:
            current_request_id.reset(token)
        self.assertIn("event: error", events[-1])
        self.assertEqual(logs.ids("A streamed health request failed"), ["trace-stream-001"])


class BackgroundReadingTests(BackgroundCase):
    def test_a_background_reading_logs_under_the_uploads_id(self):
        token = current_request_id.set("trace-upload-0001")
        try:
            with self.reading(IDENTITY, fail=True), Capture("backend.storage.service") as logs, \
                    patch.object(logging.getLogger("backend.storage.service"), "propagate", False):
                self.send()
                self.finish()
        finally:
            current_request_id.reset(token)
        self.assertEqual(logs.ids("Processing a document failed"), ["trace-upload-0001"])


if __name__ == "__main__":
    unittest.main()
