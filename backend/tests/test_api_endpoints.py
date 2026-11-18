"""Integration tests for FastAPI application endpoints in main.py."""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

with patch("backend.utils.env.find_env_file", return_value=None):  # keep the real .env out of tests
    from backend.main import app, install_actuator, install_cors
from backend.demo.demo_cases import document_success_state, benefits_success_state, benefits_failure_state
from test_graph_ocr import ocr_page
from test_ocr import UNIT, ensure_fixtures

RAW_WORKFLOWS = ("/api/v1/workflows/document-intake/run", "/api/v1/workflows/benefits/run")


def app_config(demo_mode=True, environment="development"):
    return SimpleNamespace(app=SimpleNamespace(demo_mode=demo_mode, environment=environment, version="test"))


class ApiIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_health_check_endpoint(self):
        resp = self.client.get("/api/v1/health")
        self.assertEqual(resp.status_code, 200)

        json_data = resp.json()
        self.assertEqual(json_data["status"], "SUCCESS")
        self.assertEqual(json_data["data"]["serviceStatus"], "UP")
        self.assertEqual(json_data["data"]["environment"], "development")
        self.assertIn("serverTime", json_data["meta"])

    def test_run_document_intake_workflow_via_api(self):
        doc_state = document_success_state()
        resp = self.client.post("/api/v1/workflows/document-intake/run", json={"state": doc_state})
        self.assertEqual(resp.status_code, 200)

        json_data = resp.json()
        self.assertEqual(json_data["status"], "SUCCESS")
        self.assertEqual(json_data["data"]["status"], "confirmed")
        self.assertIn("vault_record", json_data["data"])
        self.assertEqual(json_data["data"]["vault_record"]["confirmed_fields"]["annual_income"]["value"], 120000)

    def test_run_benefits_workflow_via_api(self):
        # Obtain vault record from document intake run
        doc_resp = self.client.post("/api/v1/workflows/document-intake/run", json={"state": document_success_state()})
        vault_record = doc_resp.json()["data"]["vault_record"]

        ben_state = benefits_success_state(vault_record)
        resp = self.client.post("/api/v1/workflows/benefits/run", json={"state": ben_state})
        self.assertEqual(resp.status_code, 200)

        json_data = resp.json()
        self.assertEqual(json_data["status"], "SUCCESS")
        self.assertEqual(json_data["data"]["status"], "ready_for_manual_handoff")
        self.assertIn("application_package", json_data["data"])
        self.assertEqual(
            json_data["data"]["application_package"]["scheme_id"],
            "demo_senior_support",
        )


class RawWorkflowRestrictionTests(unittest.TestCase):
    """The /workflows/*/run endpoints run any state they are sent, so they are for local demos
    only, and never read a file named in the state (fastapi-backend Decision 3)."""

    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_a_file_on_the_server_is_never_read(self):
        ensure_fixtures()
        state = document_success_state()
        document = state["document"]
        document.pop("pages")
        document.update(file_path=str(UNIT / "sharp.png"), mime_type="image/png")
        secret = ocr_page([("Name: Someone Else", 0.99), ("Private note on the server", 0.99)])
        with patch("backend.graphs.document_graph.read_document", return_value=[secret]) as reader:
            resp = self.client.post(RAW_WORKFLOWS[0], json={"state": state})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"]["code"], "BAD_REQUEST")
        self.assertNotIn("Private note", resp.text)
        reader.assert_not_called()

    def test_off_in_production_and_without_demo_mode(self):
        bodies = {RAW_WORKFLOWS[0]: {"state": document_success_state()},
                  RAW_WORKFLOWS[1]: {"state": benefits_failure_state()}}
        for config in (app_config(environment="production"), app_config(environment="Prod"),
                       app_config(demo_mode=False)):
            for path, body in bodies.items():
                with self.subTest(config=config.app, path=path), patch("backend.main.get_config", return_value=config):
                    resp = self.client.post(path, json=body)
                    self.assertEqual(resp.status_code, 404)
                    self.assertNotIn("vault_record", resp.text)

    def test_still_on_for_a_local_demo(self):
        with patch("backend.main.get_config", return_value=app_config()):
            resp = self.client.post(RAW_WORKFLOWS[1], json={"state": benefits_failure_state()})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["status"], "needs_input")


def actuator_config(environment="development", enabled=True):
    return SimpleNamespace(app=SimpleNamespace(name="SeniorLife OS", environment=environment, version="test"),
                           server=SimpleNamespace(host="127.0.0.1", port=8000),
                           actuator=SimpleNamespace(enabled=enabled))


class ActuatorTests(unittest.TestCase):
    """/actuator has no sign-in: it shows environment values, request URLs (with names in query
    strings) and lets anyone change log levels, so it is never on in production (Decision 5)."""

    def test_off_in_production_even_when_enabled(self):
        for environment in ("production", "PROD"):
            with self.subTest(environment=environment):
                fresh = FastAPI()
                self.assertFalse(install_actuator(fresh, actuator_config(environment)))
                self.assertEqual(TestClient(fresh).get("/actuator/env").status_code, 404)

    def test_off_when_disabled(self):
        fresh = FastAPI()
        self.assertFalse(install_actuator(fresh, actuator_config(enabled=False)))
        self.assertEqual(TestClient(fresh).get("/actuator/env").status_code, 404)

    def test_local_actuator_hides_api_tokens(self):
        # Pyctuator's own list hides names with key/secret/password/token, but not APIFY_API.
        fresh = FastAPI()
        tokens = {"APIFY_API": "apify-token-1234567", "GEOAPIFY_API_KEY": "geo-token-7654321",
                  "SOME_DSN": "dsn-value-555"}
        with patch.dict(os.environ, tokens):
            self.assertTrue(install_actuator(fresh, actuator_config()))
            resp = TestClient(fresh).get("/actuator/env")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("APIFY_API", resp.text)  # the name is listed, the value hidden
        for value in tokens.values():
            self.assertNotIn(value, resp.text)


class CorsTests(unittest.TestCase):
    """Only the configured web-app origins may call the API from a browser (Decision 6)."""

    def setUp(self):
        fresh = FastAPI()
        install_cors(fresh, SimpleNamespace(cors=SimpleNamespace(allowed_origins=["http://localhost:3000"])))

        @fresh.get("/ping")
        def ping():
            return {"ok": True}

        self.client = TestClient(fresh)

    def preflight(self, origin):
        return self.client.options("/ping", headers={"Origin": origin, "Access-Control-Request-Method": "GET",
                                                     "Access-Control-Request-Headers": "authorization"})

    def test_the_web_app_origin_is_allowed(self):
        resp = self.preflight("http://localhost:3000")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("access-control-allow-origin"), "http://localhost:3000")
        self.assertIn("authorization", resp.headers.get("access-control-allow-headers", "").lower())
        self.assertNotIn("access-control-allow-credentials", resp.headers)  # Bearer header, no cookies

    def test_another_origin_is_refused(self):
        self.assertEqual(self.preflight("https://evil.example").status_code, 400)
        resp = self.client.get("/ping", headers={"Origin": "https://evil.example"})
        self.assertNotIn("access-control-allow-origin", resp.headers)


if __name__ == "__main__":
    unittest.main()
