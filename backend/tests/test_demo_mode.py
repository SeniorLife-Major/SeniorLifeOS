"""Integration tests for Demo Mode endpoints and conditional Prometheus metrics."""

import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.main import app
from backend.core.config import get_config
from backend.errors import ErrorCode


class DemoModeTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_list_demo_scenarios(self):
        resp = self.client.get("/api/v1/demo/scenarios")
        self.assertEqual(resp.status_code, 200)

        json_data = resp.json()
        self.assertEqual(json_data["status"], "SUCCESS")
        self.assertIsInstance(json_data["data"], list)
        self.assertGreaterEqual(len(json_data["data"]), 4)

        # Check scenario metadata
        scenario_ids = [s["scenarioId"] for s in json_data["data"]]
        self.assertIn("doc-income-success", scenario_ids)
        self.assertIn("doc-blurry-reupload", scenario_ids)
        self.assertIn("benefits-pension-success", scenario_ids)
        self.assertIn("benefits-missing-evidence", scenario_ids)

    def test_execute_doc_income_success_scenario(self):
        resp = self.client.post("/api/v1/demo/scenarios/doc-income-success/execute")
        self.assertEqual(resp.status_code, 200)

        json_data = resp.json()
        self.assertEqual(json_data["status"], "SUCCESS")
        self.assertEqual(json_data["data"]["status"], "confirmed")
        self.assertIn("vault_record", json_data["data"])
        self.assertEqual(
            json_data["data"]["vault_record"]["confirmed_fields"]["annual_income"]["value"],
            120000,
        )

    def test_execute_doc_blurry_reupload_scenario(self):
        resp = self.client.post("/api/v1/demo/scenarios/doc-blurry-reupload/execute")
        self.assertEqual(resp.status_code, 200)

        json_data = resp.json()
        self.assertEqual(json_data["status"], "SUCCESS")
        self.assertEqual(json_data["data"]["status"], "needs_reupload")

    def test_execute_benefits_pension_success_scenario(self):
        resp = self.client.post("/api/v1/demo/scenarios/benefits-pension-success/execute")
        self.assertEqual(resp.status_code, 200)

        json_data = resp.json()
        self.assertEqual(json_data["status"], "SUCCESS")
        self.assertEqual(json_data["data"]["status"], "ready_for_manual_handoff")
        self.assertEqual(
            json_data["data"]["application_package"]["scheme_id"],
            "demo_senior_support",
        )

    def test_execute_non_existent_scenario_returns_404_envelope(self):
        resp = self.client.post("/api/v1/demo/scenarios/non-existent-123/execute")
        self.assertEqual(resp.status_code, 404)

        json_data = resp.json()
        self.assertEqual(json_data["status"], "ERROR")
        self.assertEqual(json_data["error"]["code"], ErrorCode.NOT_FOUND.value)
        self.assertIn("non-existent-123", json_data["error"]["message"])

    def test_pyctuator_actuator_endpoints(self):
        """Test that Pyctuator exposes standard Spring Boot Actuator endpoints."""
        resp = self.client.get("/actuator")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("_links", resp.json())

        health_resp = self.client.get("/actuator/health")
        self.assertEqual(health_resp.status_code, 200)
        health_json = health_resp.json()
        self.assertEqual(health_json["status"], "UP")
        self.assertIn("diskSpace", health_json["details"])

        info_resp = self.client.get("/actuator/info")
        self.assertEqual(info_resp.status_code, 200)
        info_json = info_resp.json()
        self.assertEqual(info_json["app"]["name"], "SeniorLife OS")
        self.assertEqual(info_json["build"]["version"], "1.0.0")

        metrics_resp = self.client.get("/actuator/metrics")
        self.assertEqual(metrics_resp.status_code, 200)
        self.assertIn("names", metrics_resp.json())


if __name__ == "__main__":
    unittest.main()
