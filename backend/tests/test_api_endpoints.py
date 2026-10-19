"""Integration tests for FastAPI application endpoints in main.py."""

import unittest
from fastapi.testclient import TestClient

from backend.main import app
from backend.demo.demo_cases import document_success_state, benefits_success_state


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


if __name__ == "__main__":
    unittest.main()
