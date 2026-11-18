"""Tests: accounts and profiles (storage/accounts.py).

The service tests need TEST_DATABASE_URL, like test_storage_db.py.
"""

import unittest
from unittest.mock import patch

from sqlalchemy import select

from backend.errors import ForbiddenException, NotFoundException, ValidationException
from backend.storage.accounts import AccountService, InvalidCredentials, LoginTaken, normalize_login
from backend.storage.db import make_sessions
from backend.storage.models import AuditEvent, Profile, User
from test_storage_db import DatabaseCase

MOBILE = "+919876543210"
PASSWORD = "chai-at-four-pm"


class LoginTests(unittest.TestCase):
    def test_any_usual_spelling_of_a_mobile_number(self):
        for text in ("9876543210", "98765 43210", "+91 98765-43210", "919876543210", "09876543210",
                     " (+91) 98765 43210 "):
            with self.subTest(text=text):
                self.assertEqual(normalize_login(text), MOBILE)

    def test_email_is_kept_in_lower_case(self):
        self.assertEqual(normalize_login("  Meera.Rao@Example.ORG "), "meera.rao@example.org")

    def test_anything_else_is_refused(self):
        for text in ("", "12345", "5876543210", "98765432101", "+1 9876543210", "meera", "a@b", "a b@c.org",
                     "meera@@example.org", None):
            with self.subTest(text=text), self.assertRaises(ValidationException):
                normalize_login(text)


class AccountCase(DatabaseCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sessions = make_sessions(cls.engine)
        cls.accounts = AccountService(cls.sessions)

    def audit_rows(self):
        with self.sessions() as session:
            return session.execute(select(AuditEvent).order_by(AuditEvent.id)).scalars().all()


class AccountTests(AccountCase):
    def test_register_then_sign_in_with_any_spelling(self):
        user = self.accounts.register("98765 43210", PASSWORD, "  Meera   Rao ")
        self.assertEqual((user["login"], user["display_name"], user["role"]), (MOBILE, "Meera Rao", "senior"))
        for login in ("9876543210", "+91 98765 43210"):
            with self.subTest(login=login):
                signed_in = self.accounts.sign_in(login, PASSWORD)
                self.assertEqual((signed_in["user_id"], signed_in["token_version"]), (user["user_id"], 0))
        with self.sessions() as session:
            self.assertIsNotNone(session.get(User, user["user_id"]).last_login_at)
            self.assertIsNotNone(session.get(Profile, user["user_id"]))  # made with the account

    def test_wrong_password_and_unknown_login_give_the_same_answer(self):
        self.accounts.register(MOBILE, PASSWORD, "Meera Rao")
        with self.assertRaises(InvalidCredentials) as wrong:
            self.accounts.sign_in(MOBILE, "chai-at-five-pm")
        with patch("backend.storage.accounts.burn_password_check") as burn, \
                self.assertRaises(InvalidCredentials) as unknown:
            self.accounts.sign_in("9123456789", PASSWORD)
        burn.assert_called_once()  # as slow as a real check: the time doesn't tell an account exists
        with patch("backend.storage.accounts.burn_password_check") as burn, self.assertRaises(InvalidCredentials):
            self.accounts.sign_in("not a login", PASSWORD)
        burn.assert_called_once()
        self.assertEqual((wrong.exception.status_code, wrong.exception.message),
                         (unknown.exception.status_code, unknown.exception.message))
        self.assertEqual([row.action for row in self.audit_rows()], ["account.registered", "account.sign_in_failed"])

    def test_the_password_login_and_name_are_never_stored_or_audited_in_clear(self):
        user = self.accounts.register("meera@example.org", PASSWORD, "Meera Rao")
        self.accounts.sign_in("meera@example.org", PASSWORD)
        with self.assertRaises(InvalidCredentials):
            self.accounts.sign_in("meera@example.org", "a-wrong-password")
        with self.sessions() as session:
            stored = session.get(User, user["user_id"]).password_hash
        self.assertNotIn(PASSWORD, stored)
        self.assertTrue(stored.startswith("scrypt$"))
        audit_text = repr([(row.actor_id, row.action, row.detail) for row in self.audit_rows()])
        for secret in (PASSWORD, "a-wrong-password", "meera@example.org", "Meera"):
            self.assertNotIn(secret, audit_text)

    def test_one_account_per_login(self):
        self.accounts.register(MOBILE, PASSWORD, "Meera Rao")
        with self.assertRaises(LoginTaken) as caught:
            self.accounts.register("09876543210", "another-password", "Someone Else")
        self.assertEqual(caught.exception.status_code, 409)
        self.accounts.register("MEERA@example.org", PASSWORD, "Meera Rao")
        with self.assertRaises(LoginTaken):
            self.accounts.register("meera@EXAMPLE.org", PASSWORD, "Meera Rao")

    def test_password_name_and_role_rules(self):
        for password in ("short", "x" * 129, ""):
            with self.subTest(password=len(password)), self.assertRaises(ValidationException):
                self.accounts.register(MOBILE, password, "Meera Rao")
        for name in ("", "   ", "\x00\x07", "x" * 121):
            with self.subTest(name=name[:5]), self.assertRaises(ValidationException):
                self.accounts.register(MOBILE, PASSWORD, name)
        with self.assertRaises(ValidationException):
            self.accounts.register(MOBILE, PASSWORD, "Meera Rao", role="admin")
        self.assertEqual(self.accounts.register(MOBILE, "x" * 8, "Ravi", role="family")["role"], "family")

    def test_sign_out_ends_every_older_token(self):
        user = self.accounts.register(MOBILE, PASSWORD, "Meera Rao")
        self.assertTrue(self.accounts.token_valid(user["user_id"], 0))
        self.accounts.sign_out(user["user_id"])
        self.assertFalse(self.accounts.token_valid(user["user_id"], 0))
        self.assertEqual(self.accounts.sign_in(MOBILE, PASSWORD)["token_version"], 1)
        self.assertTrue(self.accounts.token_valid(user["user_id"], 1))
        self.assertFalse(self.accounts.token_valid("no-such-user", 0))
        with self.assertRaises(NotFoundException):
            self.accounts.sign_out("no-such-user")

    def test_a_switched_off_account(self):
        user = self.accounts.register(MOBILE, PASSWORD, "Meera Rao")
        with self.sessions.begin() as session:
            session.get(User, user["user_id"]).disabled = True
        self.assertFalse(self.accounts.token_valid(user["user_id"], 0))
        with self.assertRaises(ForbiddenException):
            self.accounts.sign_in(MOBILE, PASSWORD)
        with self.assertRaises(InvalidCredentials):  # without the password nothing is told
            self.accounts.sign_in(MOBILE, "a-wrong-password")

    def test_removing_an_account_removes_its_profile(self):
        user = self.accounts.register(MOBILE, PASSWORD, "Meera Rao")
        with self.sessions.begin() as session:
            session.delete(session.get(User, user["user_id"]))
        with self.sessions() as session:
            self.assertIsNone(session.get(Profile, user["user_id"]))


class ProfileTests(AccountCase):
    def setUp(self):
        super().setUp()
        self.user_id = self.accounts.register(MOBILE, PASSWORD, "Meera Rao")["user_id"]

    def test_defaults(self):
        self.assertEqual(self.accounts.profile(self.user_id), {
            "language": "en", "home_location": None,
            "accessibility": {"font_scale": 1.0, "high_contrast": False, "voice": False},
            "consents": {"use_llm": False, "medical_cloud": False, "cloud_ocr": False},
            "consents_updated_at": None})

    def test_only_what_is_sent_changes(self):
        self.accounts.update_profile(self.user_id, {"language": "hi", "accessibility": {"font_scale": 1.5}})
        home = {"label": "Andheri West, Mumbai", "lat": 19.136, "lon": 72.829}
        profile = self.accounts.update_profile(self.user_id, {"home": home, "accessibility": {"voice": True}})
        self.assertEqual(profile["language"], "hi")
        self.assertEqual(profile["accessibility"], {"font_scale": 1.5, "high_contrast": False, "voice": True})
        self.assertEqual(profile["home_location"], home)
        self.assertEqual(self.accounts.profile(self.user_id), profile)
        self.assertIsNone(self.accounts.update_profile(self.user_id, {"home": None})["home_location"])

    def test_consents_are_recorded_when_they_change(self):
        profile = self.accounts.update_profile(self.user_id, {"consents": {"use_llm": True}})
        self.assertEqual(profile["consents"], {"use_llm": True, "medical_cloud": False, "cloud_ocr": False})
        first = profile["consents_updated_at"]
        self.assertIsNotNone(first)
        same = self.accounts.update_profile(self.user_id, {"consents": {"use_llm": True}})
        self.assertEqual(same["consents_updated_at"], first)  # nothing changed
        changes = [row.detail for row in self.audit_rows() if row.action == "profile.consents_changed"]
        self.assertEqual(changes, [{"use_llm": True, "medical_cloud": False, "cloud_ocr": False}])

    def test_bad_values_change_nothing(self):
        for changes in ({"language": "xx"}, {"accessibility": {"colour": "red"}}, {"consents": {"share_all": True}},
                        {"consents": {"use_llm": "yes"}}, {"language": "hi", "consents": {"use_llm": 1}}):
            with self.subTest(changes=changes), self.assertRaises(ValidationException):
                self.accounts.update_profile(self.user_id, changes)
        self.assertEqual(self.accounts.profile(self.user_id)["language"], "en")

    def test_saved_choices_for_a_request(self):
        self.assertEqual(self.accounts.saved_choices(self.user_id), {
            "display_name": "Meera Rao", "home": None,
            "consents": {"use_llm": False, "medical_cloud": False, "cloud_ocr": False}})
        self.accounts.update_profile(self.user_id, {"consents": {"medical_cloud": True},
                                                    "home": {"label": "Andheri West", "lat": 19.136, "lon": 72.829}})
        saved = self.accounts.saved_choices(self.user_id)
        self.assertEqual(saved["home"], {"lat": 19.136, "lon": 72.829})  # never the label: it isn't needed
        self.assertTrue(saved["consents"]["medical_cloud"])
        self.assertEqual(self.accounts.saved_choices("senior-without-account"), {
            "display_name": None, "home": None,
            "consents": {"use_llm": False, "medical_cloud": False, "cloud_ocr": False}})

    def test_no_account(self):
        for call in (lambda: self.accounts.profile("no-such-user"),
                     lambda: self.accounts.update_profile("no-such-user", {"language": "hi"})):
            with self.assertRaises(NotFoundException):
                call()


if __name__ == "__main__":
    unittest.main()
