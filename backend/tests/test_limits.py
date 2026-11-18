"""Limits on sign-in attempts, doctor searches and uploads (fastapi-backend Phase 5): the counts
in request_counts, and each service that uses them. Real PostgreSQL (TEST_DATABASE_URL).
"""

from __future__ import annotations

import threading
import unittest
from datetime import timedelta
from unittest.mock import patch

from sqlalchemy import func, select, text, update

from backend.core.config import LimitsSection
from backend.errors.exceptions import TooManyRequestsException
from backend.storage import limits
from backend.storage.accounts import AccountService, InvalidCredentials
from backend.storage.db import make_sessions
from backend.storage.health_service import HealthService
from backend.storage.models import RequestCount
from backend.storage.service import DocumentService
from test_auth_api import ApiCase
from test_health_service import CALL_QUESTION, IN_ANDHERI, FakeStructuredModel, HealthCase, final, reading
from test_privacy import FAKE_HASH_KEY, hash_key_env
from test_storage_db import IDENTITY, OWNER, DatabaseCase

WINDOW = timedelta(minutes=15)
LOGIN, PASSWORD = "meera@example.com", "a long password"


class CountCase(DatabaseCase):
    def setUp(self):
        super().setUp()
        self.sessions = make_sessions(self.engine)

    def hit(self, key, limit=3, window=WINDOW):
        with self.sessions.begin() as session:
            return limits.count(session, key, limit, window)

    def age(self, key, by):
        with self.engine.begin() as connection:
            connection.execute(update(RequestCount).where(RequestCount.key == key)
                               .values(window_start=func.now() - by))

    def keys(self):
        with self.engine.connect() as connection:
            return sorted(row[0] for row in connection.execute(select(RequestCount.key)))


class CountTests(CountCase):
    def test_within_the_limit_then_wait_until_the_window_ends(self):
        self.assertEqual([self.hit("a") for _ in range(3)], [None, None, None])
        wait = self.hit("a")
        self.assertTrue(WINDOW.total_seconds() - 5 <= wait <= WINDOW.total_seconds(), wait)
        self.assertEqual(self.hit("b"), None)  # another key counts apart
        self.age("a", by=timedelta(minutes=10))
        self.assertTrue(4 * 60 <= self.hit("a") <= 5 * 60)  # the window is not made longer
        self.age("a", by=WINDOW)
        self.assertEqual([self.hit("a") for _ in range(3)], [None, None, None])  # a new window

    def test_clear_starts_again(self):
        for _ in range(3):
            self.hit("a")
        with self.sessions.begin() as session:
            limits.clear(session, "a")
        self.assertIsNone(self.hit("a"))

    def test_counts_at_the_same_moment_never_slip_through(self):
        results, start = [], threading.Barrier(10)

        def one():
            start.wait()
            results.append(self.hit("busy", limit=4))

        threads = [threading.Thread(target=one) for _ in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        self.assertEqual((results.count(None), len(results)), (4, 10))

    def test_old_windows_are_removed_now_and_then(self):
        self.hit("old")
        self.hit("new")
        self.age("old", by=timedelta(days=1, minutes=1))
        with patch.object(limits, "_purged_at", None):
            self.hit("other")
        self.assertEqual(self.keys(), ["new", "other"])

    def test_the_sign_in_key_hides_the_login(self):
        key = limits.sign_in_key("+919876543210", "s" * 32)
        self.assertTrue(key.startswith("sign-in:"))
        self.assertNotIn("9876543210", key)
        self.assertEqual(key, limits.sign_in_key("+919876543210", "s" * 32))
        self.assertNotEqual(key, limits.sign_in_key("+919876543210", "t" * 32))
        self.assertLessEqual(len(key), 160)
        self.assertEqual((limits.wait_words(30), limits.wait_words(61), limits.wait_words(900)),
                         ("a minute", "2 minutes", "15 minutes"))


class SignInLimitTests(CountCase):
    def setUp(self):
        super().setUp()
        self.accounts = AccountService(self.sessions, LimitsSection(sign_in_attempts=3), key_secret="k" * 32)
        self.accounts.register(LOGIN, PASSWORD, "Meera Rao")

    def attempt(self, login=LOGIN, password="wrong password"):
        try:
            self.accounts.sign_in(login, password)
            return 200
        except InvalidCredentials:
            return 401
        except TooManyRequestsException as exc:
            self.assertEqual(exc.status_code, 429)
            self.assertIn("15 minutes", exc.message)
            self.assertEqual(exc.headers["Retry-After"], str(exc.details["retryAfterSeconds"]))
            return 429

    def test_after_the_attempts_even_the_right_password_waits(self):
        self.assertEqual([self.attempt() for _ in range(3)], [401, 401, 401])
        with patch("backend.storage.accounts.verify_password") as checked:
            self.assertEqual(self.attempt(password=PASSWORD), 429)
        checked.assert_not_called()  # over the limit the password isn't even checked
        self.age(self.keys()[0], by=WINDOW)
        self.assertEqual(self.attempt(password=PASSWORD), 200)

    def test_a_login_without_an_account_is_limited_the_same_way(self):
        self.assertEqual([self.attempt(login="nobody@example.com") for _ in range(4)], [401, 401, 401, 429])

    def test_the_right_password_starts_the_count_again(self):
        self.assertEqual([self.attempt(), self.attempt(), self.attempt(password=PASSWORD),
                          self.attempt(), self.attempt()], [401, 401, 200, 401, 401])

    def test_logins_count_apart_and_are_never_stored(self):
        self.accounts.register("9876543210", PASSWORD, "Ravi Rao")
        for _ in range(3):
            self.attempt()
        self.assertEqual(self.attempt(login="98765 43210", password=PASSWORD), 200)  # same tidied login, own count
        stored = " ".join(self.keys())
        for login in ("meera", "example", "9876543210"):
            self.assertNotIn(login, stored)


class DoctorSearchLimitTests(HealthCase):
    def setUp(self):
        super().setUp()
        self.limited = HealthService(self.sessions, self.checkpointer, LimitsSection(doctor_searches_per_hour=2))

    def test_over_the_limit_a_search_waits_but_an_emergency_never(self):
        for _ in range(2):
            final(self.limited.start(OWNER, "need a heart doctor", current_location=IN_ANDHERI))
        with self.assertRaises(TooManyRequestsException) as refused:
            self.limited.start(OWNER, "need a heart doctor", current_location=IN_ANDHERI)
        self.assertIn("call 112 or 108", refused.exception.message)
        _, emergency = final(self.limited.start(OWNER, "severe chest pain and I can't breathe"))
        self.assertEqual(emergency["status"], "emergency")
        with self.assertRaises(TooManyRequestsException):  # another person isn't affected; this one still waits
            self.limited.start(OWNER, "need a knee doctor", current_location=IN_ANDHERI)
        final(self.limited.start("senior-ravi", "need a heart doctor", current_location=IN_ANDHERI))

    def test_answers_count_but_the_emergency_answer_never_waits(self):
        fake = FakeStructuredModel(reading())  # the model thinks the chest pain is not happening now
        with patch("backend.agents.llm_agents.structured_model", fake):
            _, asked = final(self.limited.start(OWNER, "I had chest pain last year, need a heart doctor",
                                                use_llm=True, medical_cloud_consent=True, current_location=IN_ANDHERI))
            self.assertEqual(asked["question"], CALL_QUESTION)
            _, where = final(self.limited.start(OWNER, "need a heart doctor"))  # asks where: 2 of 2 used
            with self.assertRaises(TooManyRequestsException):
                self.limited.answer(OWNER, where["request_id"], "location", "Andheri West, Mumbai")
            _, called = final(self.limited.answer(OWNER, asked["request_id"], "call_emergency", True))
        self.assertEqual(called["status"], "emergency")


class UploadLimitTests(DatabaseCase):
    def setUp(self):
        super().setUp()
        self.limited = DocumentService(make_sessions(self.engine), self.files, self.checkpointer,
                                       LimitsSection(uploads_per_hour=2))
        self.addCleanup(self.limited.background.shutdown)

    def test_uploads_are_limited_before_anything_is_stored_but_typing_in_is_not(self):
        for _ in range(2):
            self.upload(IDENTITY, service=self.limited)
        stored = sorted(path.name for path in self.root.rglob("*") if path.is_file())
        for background in (False, True):
            with self.subTest(background=background), self.assertRaises(TooManyRequestsException) as refused:
                with self.ocr(IDENTITY):
                    self.limited.upload(OWNER, "card.png", "image/png", self.png, claimed_type="identity",
                                        background=background)
            self.assertIn("Please wait", refused.exception.message)
        self.assertEqual(sorted(path.name for path in self.root.rglob("*") if path.is_file()), stored)
        self.assertEqual(len(self.limited.list(OWNER)), 2)
        with patch("backend.utils.privacy.find_env_file", return_value=None), hash_key_env(FAKE_HASH_KEY):
            self.limited.create_manual(OWNER, "identity", {"name": "Meera Rao", "date_of_birth": "17/09/1950"})
        self.assertEqual(len(self.limited.list(OWNER)), 3)


class TooManyOverHttpTests(ApiCase):
    def setUp(self):
        super().setUp()
        self.accounts = AccountService(self.sessions, LimitsSection(sign_in_attempts=1), key_secret=self.secret)

    def test_429_with_retry_after(self):
        body = {"login": LOGIN, "password": "wrong password"}
        self.assertEqual(self.client.post("/api/v1/auth/login", json=body).status_code, 401)
        refused = self.client.post("/api/v1/auth/login", json=body)
        self.assertEqual(refused.status_code, 429)
        error = refused.json()["error"]
        self.assertEqual(error["code"], "TOO_MANY_REQUESTS")
        self.assertEqual(refused.headers["retry-after"], str(error["details"]["retryAfterSeconds"]))
        self.assertNotIn(LOGIN, refused.text)


if __name__ == "__main__":
    unittest.main()
