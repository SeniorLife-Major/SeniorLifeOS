"""Tests: doctor search requests (storage/health_service.py) on the test database, with the
encrypted PostgreSQL checkpointer.

Needs TEST_DATABASE_URL, like test_storage_db.py. The doctor list, place search and model are
stand-ins (from test_health_graph.py): nothing is sent out.
"""

import json
import unittest
from datetime import timedelta
from unittest.mock import patch

from sqlalchemy import select, update

from backend.errors import NotFoundException, ValidationException
from backend.errors.exceptions import ConflictException
from backend.graphs.health_graph import CALL_QUESTION, CLARIFYING_QUESTION, LOCATION_QUESTIONS, NO_DOCTORS
from backend.storage.db import make_sessions
from backend.storage.health_service import EXPIRED, FAILED, HealthService, now
from backend.storage.models import AuditEvent, GraphRun
from backend.storage.service import WorkflowFailed, run_config
from backend.tools.geocode import GeocodeError
from test_health_graph import (
    FAKE_DOCTORS, IN_ANDHERI, PUNE, FakePlaces, FakeStructuredModel, already_fresh, found, no_network, reading,
)
from test_storage_db import OTHER_OWNER, OWNER, DatabaseCase

PERSONAL = "I am Meera Rao, 9876543210"


def final(events):
    """(step names, the result) from a run's events."""
    events = list(events)
    return [data["step"] for name, data in events if name == "step"], events[-1][1]


class HealthCase(DatabaseCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sessions = make_sessions(cls.engine)
        cls.health = HealthService(cls.sessions, cls.checkpointer)

    def setUp(self):
        super().setUp()
        for target, stand_in in (("find_place", no_network), ("load_doctors", lambda: FAKE_DOCTORS),
                                 ("refresh_doctors", already_fresh)):
            patcher = patch(f"backend.graphs.health_graph.{target}", stand_in)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_row(self, run_id):
        with self.sessions() as session:
            return session.get(GraphRun, run_id)

    def checkpoint(self, run_id):
        return self.checkpointer.get_tuple(run_config(run_id))

    def stored_text(self):
        """Everything the database keeps about health requests, as text."""
        with self.sessions() as session:
            runs = session.execute(select(GraphRun)).scalars().all()
            events = session.execute(select(AuditEvent)).scalars().all()
        return json.dumps([run.result for run in runs] + [event.detail for event in events], default=str)


class AskingTests(HealthCase):
    def test_doctors_found_in_one_go(self):
        steps, result = final(self.health.start(OWNER, "heart doctor near me", current_location=IN_ANDHERI))
        self.assertEqual(steps[:3], ["validate_request", "safety_check", "understand_request"])
        self.assertEqual(steps[-3:], ["verify", "rank", "explain"])
        self.assertEqual(result["status"], "doctors_found")
        self.assertEqual(result["specialty"], {"key": "cardiologist", "label": "Cardiologist (heart)"})
        self.assertEqual(result["search_location"], {"label": "your current location", "source": "phone"})
        self.assertTrue(result["doctors"])
        self.assertTrue(result["answer"].startswith("Cardiologist (heart) near your current location"))
        self.assertEqual(self.health.get(OWNER, result["request_id"]), result)
        row = self.run_row(result["request_id"])
        self.assertEqual((row.status, row.outcome), ("ended", "doctors_found"))
        self.assertIsNotNone(row.ended_at)
        self.assertIsNone(self.checkpoint(result["request_id"]))  # deleted as soon as the run ended

    def test_the_request_name_and_phone_location_are_never_stored(self):
        _, result = final(self.health.start(OWNER, f"{PERSONAL}, heart doctor near me", owner_name="Meera Rao",
                                            current_location=IN_ANDHERI))
        self.assertEqual(result["status"], "doctors_found")
        stored = self.stored_text()
        for personal in ("Meera", "9876543210", "I am", str(IN_ANDHERI["lat"]), str(IN_ANDHERI["lon"])):
            self.assertNotIn(personal, stored)
        self.assertNotIn("cardiologist", json.dumps([e for e in self.audit_details()]))  # audit: no kind of doctor

    def audit_details(self):
        with self.sessions() as session:
            return [(event.action, event.detail) for event in session.execute(select(AuditEvent)).scalars()]

    def test_near_my_home_uses_the_saved_home_without_keeping_it(self):
        home = {"lat": 19.136, "lon": 72.829}
        _, result = final(self.health.start(OWNER, "heart doctor near my home", home_location=home))
        self.assertEqual(result["status"], "doctors_found")
        self.assertEqual(result["search_location"], {"label": "your home", "source": "home"})
        self.assertIn("near your home", result["answer"])
        stored = self.stored_text()
        for value in ("19.136", "72.829"):
            self.assertNotIn(value, stored)
        _, asked = final(self.health.start(OWNER, "heart doctor near my home"))  # nothing saved
        self.assertEqual(asked["status"], "needs_location")

    def test_an_emergency_by_the_rules_ends_at_once(self):
        _, result = final(self.health.start(OWNER, "I have chest pain right now and can't breathe"))
        self.assertEqual(result["status"], "emergency")
        self.assertEqual(result["emergency"]["source"], "rules")
        self.assertEqual(result["emergency"]["numbers"][0]["number"], "112")
        self.assertEqual(result["doctors"], [])
        self.assertEqual(self.run_row(result["request_id"]).status, "ended")

    def test_a_graph_failure_is_recorded_and_its_checkpoint_removed(self):
        def broken():
            raise RuntimeError("the list holds Meera's request")

        with patch("backend.graphs.health_graph.load_doctors", broken), \
                self.assertLogs("backend.storage.health_service", level="ERROR") as logs, \
                self.assertRaises(WorkflowFailed):
            final(self.health.start(OWNER, "heart doctor near me", current_location=IN_ANDHERI))
        self.assertNotIn("Meera", "\n".join(logs.output))
        [run_id] = [request["request_id"] for request in self.health.list(OWNER)]
        self.assertEqual(self.health.get(OWNER, run_id)["status"], "failed")
        self.assertEqual(self.health.get(OWNER, run_id)["notes"], [FAILED])
        self.assertIsNone(self.checkpoint(run_id))


class AnswerTests(HealthCase):
    def test_location_question_then_the_answer(self):
        _, asked = final(self.health.start(OWNER, "knee doctor"))
        run_id = asked["request_id"]
        self.assertEqual((asked["status"], asked["question"]), ("needs_location", LOCATION_QUESTIONS["where"]))
        self.assertEqual(asked["specialty"]["key"], "orthopedic")  # already known while it asks where
        self.assertEqual(self.run_row(run_id).status, "paused")
        self.assertIsNotNone(self.checkpoint(run_id))  # the request waits, encrypted
        self.assertEqual(self.health.get(OWNER, run_id), asked)

        _, result = final(self.health.answer(OWNER, run_id, "location", {"lat": 19.12014, "lon": 72.84093}))
        self.assertEqual(result["status"], "doctors_found")
        self.assertEqual(result["request_id"], run_id)
        self.assertIsNone(self.checkpoint(run_id))

    def test_a_place_in_words_with_nobody_near(self):
        _, asked = final(self.health.start(OWNER, "knee doctor"))
        with patch("backend.graphs.health_graph.find_place", FakePlaces({"Pune": found(PUNE)})):
            _, result = final(self.health.answer(OWNER, asked["request_id"], "location", "Pune"))
        self.assertEqual(result["status"], "no_doctors_nearby")
        self.assertEqual(result["search_location"]["label"], PUNE["label"])
        self.assertEqual(result["attribution"], PUNE["attribution"])
        self.assertIn(NO_DOCTORS.format(km=20), result["notes"])

    def test_technical_warnings_are_not_shown(self):
        _, asked = final(self.health.start(OWNER, "knee doctor"))
        with patch("backend.graphs.health_graph.find_place", FakePlaces(error=GeocodeError("Place search needs KEY"))):
            _, result = final(self.health.answer(OWNER, asked["request_id"], "location", "Pune"))
        self.assertEqual(result["status"], "location_not_found")
        self.assertNotIn("KEY", json.dumps(result))
        self.assertEqual(len(result["notes"]), 1)

    def test_the_emergency_question_only_the_person_can_clear(self):
        fake = FakeStructuredModel(reading())  # the model thinks the chest pain is not happening now
        with patch("backend.agents.llm_agents.structured_model", fake):
            query = "I had chest pain last year, need a heart doctor"
            _, asked = final(self.health.start(OWNER, query, use_llm=True, medical_cloud_consent=True,
                                               current_location=IN_ANDHERI))
            self.assertEqual(asked["status"], "needs_emergency_confirmation")
            self.assertEqual(asked["question"], CALL_QUESTION)
            self.assertEqual(asked["emergency"]["numbers"][0]["number"], "112")  # shown while it asks
            _, result = final(self.health.answer(OWNER, asked["request_id"], "call_emergency", False))
            self.assertEqual(result["status"], "doctors_found")
            _, again = final(self.health.start(OWNER, query, use_llm=True, medical_cloud_consent=True))
            _, called = final(self.health.answer(OWNER, again["request_id"], "call_emergency", True))
        self.assertEqual((called["status"], called["emergency"]["source"]), ("emergency", "person"))
        self.assertEqual(len(fake.prompts), 2)  # one reading per request; not read again on answer

    def test_clarification_then_location(self):
        _, asked = final(self.health.start(OWNER, "please help me", current_location=IN_ANDHERI))
        self.assertEqual((asked["status"], asked["question"]), ("needs_clarification", CLARIFYING_QUESTION))
        _, where = final(self.health.answer(OWNER, asked["request_id"], "clarification", "knee pain"))
        self.assertEqual((where["status"], where["question"]), ("needs_location", LOCATION_QUESTIONS["where_or_here"]))
        _, result = final(self.health.answer(OWNER, asked["request_id"], "location", "near me"))
        self.assertEqual(result["status"], "doctors_found")

    def test_one_answer_of_the_right_kind_once(self):
        _, asked = final(self.health.start(OWNER, "knee doctor"))
        run_id = asked["request_id"]
        with self.assertRaises(ValidationException) as wrong:
            self.health.answer(OWNER, run_id, "clarification", "knee")
        self.assertEqual(wrong.exception.details, {"waitingFor": "location"})
        self.assertEqual(self.run_row(run_id).status, "paused")  # still waiting
        with self.sessions.begin() as session:  # another answer is being applied right now
            session.execute(update(GraphRun).where(GraphRun.id == run_id).values(status="running"))
        with self.assertRaises(ConflictException):
            self.health.answer(OWNER, run_id, "location", "near me")
        with self.sessions.begin() as session:
            session.execute(update(GraphRun).where(GraphRun.id == run_id).values(status="paused"))
        final(self.health.answer(OWNER, run_id, "location", {"lat": 19.12, "lon": 72.84}))
        with self.assertRaises(ConflictException):  # it has ended
            self.health.answer(OWNER, run_id, "location", {"lat": 19.12, "lon": 72.84})

    def test_someone_elses_request_is_not_found(self):
        _, asked = final(self.health.start(OWNER, "knee doctor"))
        for call in (lambda: self.health.get(OTHER_OWNER, asked["request_id"]),
                     lambda: self.health.answer(OTHER_OWNER, asked["request_id"], "location", "Pune"),
                     lambda: self.health.get(OWNER, "no-such-request")):
            with self.assertRaises(NotFoundException):
                call()
        self.assertEqual(self.health.list(OTHER_OWNER), [])


class KeepingTests(HealthCase):
    def age(self, run_id, **ages):
        with self.sessions.begin() as session:
            session.execute(update(GraphRun).where(GraphRun.id == run_id)
                            .values(**{column: now() - age for column, age in ages.items()}))

    def test_list_is_mine_and_newest_first(self):
        first = final(self.health.start(OWNER, "knee doctor"))[1]["request_id"]
        second = final(self.health.start(OWNER, "I have chest pain right now"))[1]["request_id"]
        final(self.health.start(OTHER_OWNER, "knee doctor"))
        self.age(first, created_at=timedelta(minutes=5))
        listed = self.health.list(OWNER)
        self.assertEqual([item["request_id"] for item in listed], [second, first])
        self.assertEqual([item["status"] for item in listed], ["emergency", "needs_location"])
        self.assertEqual(listed[1]["specialty"]["key"], "orthopedic")
        self.assertEqual(len(self.health.list(OWNER, limit=1)), 1)

    def test_an_unanswered_question_expires_and_its_checkpoint_goes(self):
        run_id = final(self.health.start(OWNER, "knee doctor"))[1]["request_id"]
        self.age(run_id, updated_at=timedelta(hours=25))
        with self.assertRaises(ConflictException) as caught:
            self.health.answer(OWNER, run_id, "location", "near me")
        self.assertEqual(caught.exception.message, EXPIRED)
        self.assertEqual(self.health.get(OWNER, run_id)["status"], "expired")
        self.assertIsNone(self.checkpoint(run_id))

    def test_a_run_cut_off_midway_fails(self):
        run_id = final(self.health.start(OWNER, "knee doctor"))[1]["request_id"]
        with self.sessions.begin() as session:
            session.execute(update(GraphRun).where(GraphRun.id == run_id).values(status="running"))
        self.age(run_id, updated_at=timedelta(hours=2))
        self.health.purge_expired()
        self.assertEqual(self.health.get(OWNER, run_id)["status"], "failed")
        self.assertIsNone(self.checkpoint(run_id))

    def test_results_are_removed_after_30_days(self):
        old = final(self.health.start(OWNER, "I have chest pain right now"))[1]["request_id"]
        recent = final(self.health.start(OWNER, "I have chest pain right now"))[1]["request_id"]
        self.age(old, created_at=timedelta(days=31))
        self.age(recent, created_at=timedelta(days=29))
        with self.assertRaises(NotFoundException):  # hidden at once, even before it is removed
            self.health.get(OWNER, old)
        self.health.purge_expired()
        self.assertIsNone(self.run_row(old))
        self.assertEqual([item["request_id"] for item in self.health.list(OWNER)], [recent])


if __name__ == "__main__":
    unittest.main()
