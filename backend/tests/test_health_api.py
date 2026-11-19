"""Tests: the doctor search API (api/health_requests.py) and the services made in api/deps.py.

Uses the real AccountService and HealthService on the test database (TEST_DATABASE_URL, like
test_storage_db.py). The doctor list, place search and model are stand-ins: nothing is sent out.
"""

import json
import os
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.core.config import LimitsSection
from backend.api import deps, health_requests
from backend.api.deps import StorageUnavailable
from backend.storage.health_service import HealthService
from backend.tools.specialties import SPECIALTIES
from backend.utils.checkpoints import close_checkpointer
from test_auth_api import CONFIG_USERS, ApiCase, config
from test_health_graph import FAKE_DOCTORS, IN_ANDHERI, FakeStructuredModel, already_fresh, no_network, reading
from test_storage_db import URL, DatabaseCase

HERE = {"lat": IN_ANDHERI["lat"], "lon": IN_ANDHERI["lon"]}


def no_model(*args, **kwargs):
    raise RuntimeError("no model in tests")


def sse_events(text):
    events = []
    for block in text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((fields["event"], json.loads(fields["data"])))
    return events


class HealthApiCase(ApiCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.health = HealthService(cls.sessions, cls.checkpointer)

    def setUp(self):
        super().setUp()
        patcher = patch("backend.api.health_requests.get_config", return_value=config())
        patcher.start()
        self.addCleanup(patcher.stop)
        for target, stand_in in (("find_place", no_network), ("load_doctors", lambda: FAKE_DOCTORS),
                                 ("refresh_doctors", already_fresh)):
            patcher = patch(f"backend.graphs.health_graph.{target}", stand_in)
            patcher.start()
            self.addCleanup(patcher.stop)
        # Doctor search always asks the model now; no test may reach the real one (a key in .env
        # would be used). Without it the request is read on this machine, as before. A test that
        # wants the model patches its own fake over this.
        patcher = patch("backend.agents.llm_agents.structured_model", no_model)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app.include_router(health_requests.router)
        self.app.dependency_overrides[deps.get_health_service] = lambda: self.health
        self.headers = self.bearer(self.token())

    def ask(self, body, headers=None, stream=False):
        extra = {"Accept": "text/event-stream"} if stream else {}
        return self.client.post("/api/v1/health-requests", json=body, headers={**(headers or self.headers), **extra})

    def answer(self, request_id, body, stream=False):
        extra = {"Accept": "text/event-stream"} if stream else {}
        return self.client.post(f"/api/v1/health-requests/{request_id}/answer", json=body,
                                headers={**self.headers, **extra})


class AskTests(HealthApiCase):
    def test_find_a_doctor_then_see_it_again(self):
        response = self.ask({"query": "heart doctor near me", "currentLocation": HERE})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        data = response.json()["data"]
        self.assertEqual(data["status"], "doctors_found")
        self.assertEqual(data["specialty"], {"key": "cardiologist", "label": "Cardiologist (heart)"})
        self.assertEqual(data["searchLocation"], {"label": "your current location", "source": "phone"})
        first = data["doctors"][0]
        self.assertTrue({"doctorId", "distanceKm", "registrationLevel", "reasons", "clinic"} <= set(first))
        self.assertIn("name", first["clinic"])
        self.assertTrue(data["answer"])
        again = self.client.get(f"/api/v1/health-requests/{data['requestId']}", headers=self.headers)
        self.assertEqual(again.json()["data"], data)
        listed = self.client.get("/api/v1/health-requests", headers=self.headers).json()["data"]
        self.assertEqual([(item["requestId"], item["status"]) for item in listed], [(data["requestId"], "doctors_found")])

    def test_an_emergency(self):
        data = self.ask({"query": "I have chest pain right now"}).json()["data"]
        self.assertEqual(data["status"], "emergency")
        self.assertEqual(data["emergency"]["numbers"][0], {"number": "112",
                                                           "for": "Any emergency (police, fire, ambulance)"})

    def test_the_model_always_reads_the_request_and_the_name_is_hidden_from_it(self):
        fake = FakeStructuredModel(reading(search_near="current_location"))
        refreshed = []

        def refresh(place, specialty, doctors):
            refreshed.append(specialty)
            return already_fresh(place, specialty, doctors)

        with patch("backend.agents.llm_agents.structured_model", fake), \
                patch("backend.graphs.health_graph.refresh_doctors", refresh):
            # Nothing saved: every consent is off, and still the model reads it (they are for documents).
            data = self.ask({"query": "I am Meera Rao, I need a heart doctor near me",
                             "currentLocation": HERE}).json()["data"]
            self.assertEqual(data["status"], "doctors_found")
            self.assertEqual(len(fake.prompts), 1)
            self.assertNotIn("Meera", str(fake.prompts[0]))  # the account's name is hidden
            self.assertEqual(refreshed, ["cardiologist"])  # the doctor list may be refreshed too
            self.client.patch("/api/v1/me/profile", json={"consents": {"useLlm": False, "medicalCloud": False}},
                              headers=self.headers)
            self.ask({"query": "heart doctor near me", "currentLocation": HERE})
            self.assertEqual(len(fake.prompts), 2)  # the document switches don't stop it
            self.ask({"query": "heart doctor near me", "currentLocation": HERE, "useLlm": False})
            self.assertEqual(len(fake.prompts), 2)  # only a request that says no itself (the app never does)
            self.assertEqual(refreshed, ["cardiologist", "cardiologist", "cardiologist"])  # it said no to the model only
            self.ask({"query": "heart doctor near me", "currentLocation": HERE, "medicalCloudConsent": False})
            self.assertEqual(len(refreshed), 3)  # no refresh when the request says no to it

    def test_near_my_home_uses_the_home_saved_in_the_profile(self):
        asked = self.ask({"query": "heart doctor near my home"}).json()["data"]
        self.assertEqual(asked["status"], "needs_location")  # no home saved yet: asked
        self.client.patch("/api/v1/me/profile", json={"homeLocation": {"lat": 19.1361, "lon": 72.8294}},
                          headers=self.headers)
        data = self.ask({"query": "heart doctor near my home", "currentLocation": HERE}).json()["data"]
        self.assertEqual(data["status"], "doctors_found")
        self.assertEqual(data["searchLocation"], {"label": "your home", "source": "home"})
        where = self.ask({"query": "heart doctor"}).json()["data"]  # nothing said about where: asked, home offered
        self.assertIn('"home"', where["question"])
        answered = self.answer(where["requestId"], {"location": "ghar"}).json()["data"]
        self.assertEqual(answered["searchLocation"], {"label": "your home", "source": "home"})

    def test_bad_requests(self):
        for body in ({"query": ""}, {"query": "x" * 2001}, {}, {"query": "knee", "currentLocation": {"lat": 200, "lon": 1}},
                     {"query": "knee", "useLlm": "yes"}, {"query": "knee", "currentLocation": {"lat": 19.1}}):
            with self.subTest(body=str(body)[:50]):
                self.assertEqual(self.ask(body).status_code, 422)
        self.assertEqual(self.client.get("/api/v1/health-requests?limit=0", headers=self.headers).status_code, 422)

    def test_sign_in_and_ownership(self):
        self.assertEqual(self.client.post("/api/v1/health-requests", json={"query": "knee doctor"}).status_code, 401)
        self.assertEqual(self.client.get("/api/v1/health/specialties").status_code, 401)
        request_id = self.ask({"query": "knee doctor"}).json()["data"]["requestId"]
        other = self.bearer(self.token(login="9123456789", name="Ravi"))
        self.assertEqual(self.client.get(f"/api/v1/health-requests/{request_id}", headers=other).status_code, 404)
        response = self.client.post(f"/api/v1/health-requests/{request_id}/answer", json={"location": "Pune"},
                                    headers=other)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.get("/api/v1/health-requests", headers=other).json()["data"], [])

    def test_specialties(self):
        data = self.client.get("/api/v1/health/specialties", headers=self.headers).json()["data"]
        self.assertEqual({item["key"]: item["label"] for item in data}, SPECIALTIES)


class AnswerTests(HealthApiCase):
    def test_answer_the_location_question_once(self):
        asked = self.ask({"query": "knee doctor"}).json()["data"]
        self.assertEqual(asked["status"], "needs_location")
        self.assertTrue(asked["question"])
        response = self.answer(asked["requestId"], {"location": HERE})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"]["status"], "doctors_found")
        again = self.answer(asked["requestId"], {"location": HERE})
        self.assertEqual(again.status_code, 409)

    def test_the_answer_must_be_one_and_the_kind_asked_for(self):
        request_id = self.ask({"query": "knee doctor"}).json()["data"]["requestId"]
        wrong = self.answer(request_id, {"clarification": "knee pain"})
        self.assertEqual(wrong.status_code, 422)
        self.assertEqual(wrong.json()["error"]["details"], {"waitingFor": "location"})
        for body in ({}, {"location": "Pune", "clarification": "knee"}, {"callEmergency": "no"}, {"location": ""},
                     {"location": {"lat": 19.1}}):
            with self.subTest(body=body):
                self.assertEqual(self.answer(request_id, body).status_code, 422)
        self.assertEqual(self.answer("no-such-request", {"location": "Pune"}).status_code, 404)


class StreamTests(HealthApiCase):
    def test_steps_then_the_result(self):
        response = self.ask({"query": "heart doctor near me", "currentLocation": HERE}, stream=True)
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        self.assertEqual(response.headers["cache-control"], "no-store")
        events = sse_events(response.text)
        names = [name for name, _ in events]
        self.assertEqual(names[-1], "result")
        self.assertEqual(set(names[:-1]), {"step"})
        self.assertEqual([data["step"] for _, data in events[:3]], ["validate_request", "safety_check",
                                                                    "understand_request"])
        result = events[-1][1]
        self.assertEqual((result["status"], result["data"]["status"]), ("SUCCESS", "doctors_found"))
        self.assertIn("serverTime", result["meta"])
        saved = self.client.get(f"/api/v1/health-requests/{result['data']['requestId']}", headers=self.headers)
        self.assertEqual(saved.json()["data"], result["data"])

    def test_an_answer_can_be_streamed_too(self):
        request_id = self.ask({"query": "knee doctor"}).json()["data"]["requestId"]
        events = sse_events(self.answer(request_id, {"location": HERE}, stream=True).text)
        self.assertEqual(events[-1][1]["data"]["status"], "doctors_found")

    def test_a_failure_is_an_error_event_without_details(self):
        def broken():
            raise RuntimeError("Meera's request text")

        with patch("backend.graphs.health_graph.load_doctors", broken), self.assertLogs("backend", level="ERROR"):
            response = self.ask({"query": "heart doctor near me", "currentLocation": HERE}, stream=True)
        name, data = sse_events(response.text)[-1]
        self.assertEqual((name, data["error"]["code"]), ("error", "WORKFLOW_EXECUTION_FAILED"))
        self.assertNotIn("Meera", response.text)

    def test_errors_found_before_the_search_starts_are_plain_json(self):
        response = self.answer("no-such-request", {"location": "Pune"}, stream=True)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "NOT_FOUND")

    def test_the_search_finishes_and_is_saved_when_the_caller_goes_away(self):
        release, started = threading.Event(), threading.Event()

        def slow_refresh(place, specialty, doctors):
            started.set()
            release.wait(10)
            return already_fresh(place, specialty, doctors)

        events = self.health.start("senior-meera", "heart doctor near me", current_location=HERE,
                                   medical_cloud_consent=True)  # consent: the refresh step runs
        with patch("backend.graphs.health_graph.refresh_doctors", slow_refresh):
            body = health_requests.stream(events)
            self.assertIn("event: step", next(body))
            self.assertTrue(started.wait(10))
            body.close()  # the caller has gone
            release.set()
            [summary] = self.wait_for_end("senior-meera")
        self.assertEqual(summary["status"], "doctors_found")

    def wait_for_end(self, owner, seconds=10):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            listed = self.health.list(owner)
            if listed and listed[0]["status"] != "running":
                return listed
            time.sleep(0.05)
        self.fail("the search did not finish")


class ServiceSetupTests(DatabaseCase):
    """api/deps builds the services from the settings."""

    def setUp(self):
        super().setUp()
        for patcher in (patch.dict(deps._made, clear=True),
                        patch("backend.storage.settings.find_env_file", return_value=None),
                        patch("backend.utils.checkpoints.find_env_file", return_value=None),
                        patch("backend.api.deps.get_config", return_value=SimpleNamespace(
                            database=SimpleNamespace(pool_size=2, max_overflow=0, echo=False),
                            security=SimpleNamespace(token_secret="s" * 32), limits=LimitsSection()))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_no_checkpoint_key_names_the_setting(self):
        with patch.dict(os.environ, {"DATABASE_URL": URL}), patch.dict(os.environ):
            os.environ.pop("SENIORLIFE_CHECKPOINT_KEY", None)
            with self.assertRaises(StorageUnavailable) as caught:
                deps.get_health_service()
        self.assertIn("SENIORLIFE_CHECKPOINT_KEY", caught.exception.message)
        self.assertNotIn("health", deps._made)  # tried again next time

    def test_both_services_share_one_pool(self):
        key = bytes(range(32)).hex()
        with patch.dict(os.environ, {"DATABASE_URL": URL, "SENIORLIFE_CHECKPOINT_KEY": key}):
            health, accounts = deps.get_health_service(), deps.get_account_service()
        try:
            self.assertIs(health.sessions, accounts.sessions)
            self.assertIs(deps.get_health_service(), health)
        finally:
            close_checkpointer(health.checkpointer)
            health.sessions.kw["bind"].dispose()

    def test_no_database_setting(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(StorageUnavailable) as caught:
            deps.get_account_service()
        self.assertIn("DATABASE_URL", caught.exception.message)


if __name__ == "__main__":
    unittest.main()
