"""Tests: the benefit schemes API (api/benefits.py) and its service (storage/benefits_service.py).

- RouteTests: the routes, with the real graph and the service's own views, on papers kept in
  memory (no database), so every answer's shape is checked against real results.
- ServiceTests: the real BenefitsService and DocumentService on the test database
  (TEST_DATABASE_URL, like test_storage_db.py): papers typed in, screened, checked, approved.
"""

import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.api import benefits, deps
from backend.errors import NotFoundException, ValidationException, register_global_error_handlers
from backend.errors.exceptions import ConflictException
from backend.graphs.benefits_graph import build_benefits_graph
from backend.storage.benefits_service import BenefitsService, result_view
from backend.storage.models import AuditEvent, GraphRun
from backend.storage.db import make_sessions
from test_benefits_schemes import BOTH, death_certificate, kamla_papers
from test_privacy import FAKE_HASH_KEY, hash_key_env
from test_storage_db import DatabaseCase

OWNER = "senior-kamla"
TODAY = date(2026, 11, 21)
CONFIG_USERS = ("backend.api.deps.get_config", "backend.api.benefits.get_config",
                "backend.errors.global_error_handler.get_config")


def config():
    return SimpleNamespace(app=SimpleNamespace(environment="development", version="test"),
                           security=SimpleNamespace(token_secret="x" * 40, token_ttl_minutes=720))


class MemoryBenefits(BenefitsService):
    """The real service's graph and views, with papers and checks kept in memory."""

    def __init__(self, papers):
        self.graph = build_benefits_graph()
        self.papers = papers
        self.runs = {}

    def check(self, owner_id, scheme_id=None, declarations=None, *, owner_name=None, as_of=None):
        mode = "screen" if scheme_id is None else "apply"
        if scheme_id is not None and scheme_id not in {item["scheme_id"] for item in self.schemes()}:
            raise NotFoundException("This scheme is not in the demo list")
        check_id = f"check-{len(self.runs) + 1}"
        values = self.graph.invoke({
            "actor_id": owner_id, "case_id": check_id, "case_version": 1, "expected_case_version": 1,
            "mode": mode, "jurisdiction": "IN", "as_of": TODAY.isoformat(), "documents": self.papers,
            "declarations": declarations or {}, **({"scheme_id": scheme_id} if scheme_id else {})})
        self.runs[check_id] = (owner_id, result_view(check_id, mode, values, TODAY, declarations or {}))
        return self.get(owner_id, check_id)

    def get(self, owner_id, check_id):
        owner, view = self.runs.get(check_id, (None, None))
        if owner != owner_id:
            raise NotFoundException("This scheme check was not found")
        return {**view, "created_at": datetime(2026, 11, 21, tzinfo=timezone.utc)}

    def list(self, owner_id, limit=20):
        return [{"check_id": key, "mode": view["mode"], "status": view["status"], "scheme_id": view["scheme_id"],
                 "created_at": datetime(2026, 11, 21, tzinfo=timezone.utc)}
                for key, (owner, view) in self.runs.items() if owner == owner_id][:limit]


class RouteTests(unittest.TestCase):
    def setUp(self):
        for target in CONFIG_USERS:
            patcher = patch(target, return_value=config())
            patcher.start()
            self.addCleanup(patcher.stop)
        app = FastAPI()
        register_global_error_handlers(app)
        app.include_router(benefits.router)
        self.service = MemoryBenefits([*kamla_papers(), death_certificate()])
        app.dependency_overrides[deps.get_benefits_service] = lambda: self.service
        app.dependency_overrides[deps.get_account_service] = lambda: SimpleNamespace(
            saved_choices=lambda actor: {"display_name": "Kamla Devi"})
        self.client = TestClient(app, raise_server_exceptions=False)
        self.headers = {"X-Actor-Id": OWNER}

    def post(self, path, body):
        return self.client.post(f"/api/v1/benefits{path}", json=body, headers=self.headers)

    def test_the_catalogue(self):
        response = self.client.get("/api/v1/benefits/schemes", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        schemes = {item["schemeId"]: item for item in response.json()["data"]}
        self.assertEqual(set(schemes), {"senior_health_cover", "old_age_pension", "widow_pension",
                                        "disability_pension"})
        widow = schemes["widow_pension"]
        self.assertEqual(widow["requiredDocuments"], ["identity", "death_certificate", "ration_card", "bank_passbook"])
        self.assertEqual(widow["declarations"][0], {"key": "not_remarried", "label": "I have not married again"})
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_screening_answers_in_camel_case(self):
        response = self.post("/checks", {})
        self.assertEqual(response.status_code, 201, response.text)
        data = response.json()["data"]
        self.assertEqual((data["mode"], data["status"], data["asOf"]), ("screen", "screened", "2026-11-21"))
        widow = next(item for item in data["schemes"] if item["schemeId"] == "widow_pension")
        self.assertEqual(widow["status"], "likely")
        self.assertEqual(widow["toConfirm"][0], {"key": "not_remarried", "label": "I have not married again",
                                                 "confirmed": False})
        self.assertEqual(next(c for c in widow["criteria"] if c["criterionId"] == "age_from")["value"], 68)
        self.assertIsNone(data["package"])

    def test_one_scheme_with_answers_prepares_a_package(self):
        response = self.post("/checks", {"schemeId": "widow_pension", "declarations": BOTH})
        data = response.json()["data"]
        self.assertEqual(data["status"], "awaiting_review", response.text)
        self.assertEqual(data["package"]["status"], "proposed")
        self.assertRegex(data["package"]["actionId"], r"^[0-9a-f]{16}$")
        self.assertEqual([item["documentType"] for item in data["package"]["documents"]],
                         ["identity", "death_certificate", "ration_card", "bank_passbook"])
        self.assertEqual(data["declarations"], BOTH)

    def test_bad_requests(self):
        self.assertEqual(self.post("/checks", {"schemeId": "made_up"}).status_code, 404)
        self.assertEqual(self.post("/checks", {"declarations": {"not_remarried": "yes"}}).status_code, 422)
        self.assertEqual(self.post("/checks/check-1/approve", {"actionId": "not-hex"}).status_code, 422)
        no_actor = self.client.post("/api/v1/benefits/checks", json={})
        self.assertEqual(no_actor.status_code, 401)

    def test_someone_elses_check_is_not_found(self):
        check_id = self.post("/checks", {}).json()["data"]["checkId"]
        response = self.client.get(f"/api/v1/benefits/checks/{check_id}", headers={"X-Actor-Id": "senior-other"})
        self.assertEqual(response.status_code, 404)
        mine = self.client.get("/api/v1/benefits/checks", headers=self.headers).json()["data"]
        self.assertEqual([item["checkId"] for item in mine], [check_id])


class ServiceTests(DatabaseCase):  # skipped, like every DatabaseCase, without TEST_DATABASE_URL
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sessions = make_sessions(cls.engine)
        cls.benefits = BenefitsService(cls.sessions)

    def setUp(self):
        super().setUp()
        patcher = patch("backend.storage.benefits_service.india_today", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)

    def type_in(self, papers):
        """Each paper typed in by its owner (Decision #42): confirmed, IDs masked and hashed."""
        ids = {}
        with patch("backend.utils.privacy.find_env_file", return_value=None), hash_key_env(FAKE_HASH_KEY):
            for paper in papers:
                fields = {key: item["value"] for key, item in paper["confirmed_fields"].items()
                          if not str(item["value"]).startswith("X")}  # masked stand-ins are left out
                if paper["type"] == "identity":
                    fields["aadhaar_number"] = "234567890124"
                if paper["type"] == "bank_passbook":
                    fields["account_number"] = "987654321012"
                document = self.service.create_manual(OWNER, paper["type"], fields)
                ids[paper["type"]] = document["document_id"]
        return ids

    def test_screen_then_apply_then_approve(self):
        ids = self.type_in([*kamla_papers(), death_certificate()])
        screened = self.benefits.check(OWNER)
        self.assertEqual(screened["status"], "screened")
        widow = next(item for item in screened["schemes"] if item["scheme_id"] == "widow_pension")
        self.assertEqual(widow["status"], "likely")
        checked = self.benefits.check(OWNER, "widow_pension", BOTH)
        self.assertEqual(checked["status"], "awaiting_review")
        self.assertEqual([item["document_id"] for item in checked["package"]["documents"]],
                         [ids["identity"], ids["death_certificate"], ids["ration_card"], ids["bank_passbook"]])
        approved = self.benefits.approve(OWNER, checked["check_id"], checked["package"]["action_id"],
                                         task_title="Go to the ward office on Monday")
        self.assertEqual(approved["status"], "ready_for_manual_handoff")
        self.assertEqual(approved["package"]["tasks"], [{"title": "Go to the ward office on Monday"}])
        with self.assertRaises(ConflictException):  # once only
            self.benefits.approve(OWNER, checked["check_id"], checked["package"]["action_id"])
        self.assertEqual([item["check_id"] for item in self.benefits.list(OWNER)][0], checked["check_id"])
        with self.sessions() as session:
            actions = [row.action for row in session.execute(select(AuditEvent)).scalars()]
            stored = " ".join(str(run.result) for run in session.execute(select(GraphRun)).scalars())
        self.assertIn("benefits.approved", actions)
        for private in ("Kamla", "Ramesh", "Raju", "234567890124", "987654321012"):
            self.assertNotIn(private, stored)  # no names or ID numbers in what is kept

    def test_an_approval_after_the_papers_changed_is_refused(self):
        ids = self.type_in(kamla_papers())
        checked = self.benefits.check(OWNER, "old_age_pension", {"no_other_pension": True})
        self.assertEqual(checked["status"], "awaiting_review")
        with patch("backend.utils.privacy.find_env_file", return_value=None), hash_key_env(FAKE_HASH_KEY):
            self.service.replace_manual(OWNER, ids["ration_card"], {
                "head_of_family": "Raju Prasad", "card_category": "AAY", "residence_state": "MH",
                "family_members": "Raju Prasad, Kamla Devi"})
        with self.assertRaises(ConflictException):
            self.benefits.approve(OWNER, checked["check_id"], checked["package"]["action_id"])

    def test_checks_are_private_and_validated(self):
        self.type_in(kamla_papers())
        checked = self.benefits.check(OWNER)
        with self.assertRaises(NotFoundException):
            self.benefits.get("senior-other", checked["check_id"])
        with self.assertRaises(NotFoundException):
            self.benefits.check(OWNER, "demo_senior_support")  # not listed
        with self.assertRaises(ValidationException):
            self.benefits.check(OWNER, "old_age_pension", {"not_remarried": True})  # not this scheme's
        with self.assertRaises(ConflictException):
            self.benefits.approve(OWNER, checked["check_id"], "0" * 16)  # a screening has no package


if __name__ == "__main__":
    unittest.main()
