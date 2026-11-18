"""Tests: sign-in API and who is calling (api/auth.py, api/deps.py).

Uses the real AccountService on the test database (TEST_DATABASE_URL, like test_storage_db.py).
"""

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import jwt
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from backend.api import auth, deps, documents, profile
from backend.core.security import create_access_token
from backend.errors import register_global_error_handlers
from backend.storage.models import User
from test_accounts import AccountCase

SECRET = "a-test-secret-that-is-long-enough-0123456789"
PLACEHOLDER = "change-me-in-production-secret-key-32-chars-min"
PASSWORD = "chai-at-four-pm"
CONFIG_USERS = ("backend.api.deps.get_config", "backend.api.auth.get_config", "backend.api.profile.get_config",
                "backend.api.documents.get_config", "backend.errors.global_error_handler.get_config")


def config(environment="development", secret=SECRET):
    return SimpleNamespace(app=SimpleNamespace(environment=environment, version="test"),
                           security=SimpleNamespace(token_secret=secret, token_ttl_minutes=720))


class FakeDocuments:
    """Records whose documents were asked for."""

    def __init__(self):
        self.owners = []

    def list(self, owner, document_type=None):
        self.owners.append(owner)
        return []


class ApiCase(AccountCase):
    environment = "development"
    secret = SECRET

    def setUp(self):
        super().setUp()
        self.use_config(self.environment, self.secret)
        app = FastAPI()
        register_global_error_handlers(app)
        for router in (auth.router, profile.router, documents.router):
            app.include_router(router)
        app.dependency_overrides[deps.get_account_service] = lambda: self.accounts
        app.dependency_overrides[deps.account_lookup] = lambda: (lambda: self.accounts)
        self.documents = FakeDocuments()
        app.dependency_overrides[documents.get_document_service] = lambda: self.documents
        self.app = app
        self.client = TestClient(app, raise_server_exceptions=False)

    def use_config(self, environment, secret):
        for target in CONFIG_USERS:
            patcher = patch(target, return_value=config(environment, secret))
            patcher.start()
            self.addCleanup(patcher.stop)

    def register(self, login="98765 43210", password=PASSWORD, name="Meera Rao", **extra):
        return self.client.post("/api/v1/auth/register",
                                json={"login": login, "password": password, "displayName": name, **extra})

    def login(self, login="9876543210", password=PASSWORD):
        return self.client.post("/api/v1/auth/login", json={"login": login, "password": password})

    @staticmethod
    def bearer(token):
        return {"Authorization": f"Bearer {token}"}

    def token(self, **register_args):
        response = self.register(**register_args)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["data"]["accessToken"]


class SignInTests(ApiCase):
    def test_register_sign_in_me_and_sign_out(self):
        response = self.register()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.headers["cache-control"], "no-store")
        session = response.json()["data"]
        self.assertEqual(session["user"]["login"], "+919876543210")
        self.assertEqual((session["tokenType"], session["expiresIn"]), ("bearer", 720 * 60))
        self.assertNotIn(PASSWORD, response.text)
        first = session["accessToken"]

        me = self.client.get("/api/v1/auth/me", headers=self.bearer(first))
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["data"], session["user"])

        second = self.login("+91 98765-43210")
        self.assertEqual(second.status_code, 200)
        second_token = second.json()["data"]["accessToken"]
        self.assertEqual(self.client.post("/api/v1/auth/logout", headers=self.bearer(second_token)).status_code, 200)
        for token in (first, second_token):  # signing out ends every token of the account
            response = self.client.get("/api/v1/auth/me", headers=self.bearer(token))
            self.assertEqual(response.status_code, 401)
            self.assertIn("sign in again", response.json()["error"]["message"])
        third = self.login().json()["data"]["accessToken"]
        self.assertEqual(self.client.get("/api/v1/auth/me", headers=self.bearer(third)).status_code, 200)

    def test_wrong_password_and_unknown_login_look_the_same(self):
        self.register()
        wrong, unknown = self.login(password="chai-at-five-pm"), self.login(login="9123456789")
        self.assertEqual((wrong.status_code, unknown.status_code), (401, 401))
        self.assertEqual(wrong.json()["error"], unknown.json()["error"])
        self.assertEqual(wrong.json()["error"]["code"], "INVALID_CREDENTIALS")
        self.assertNotIn("chai-at-five-pm", wrong.text)

    def test_one_account_per_login(self):
        self.register()
        again = self.register(login="09876543210", name="Someone Else")
        self.assertEqual(again.status_code, 409)
        self.assertEqual(again.json()["error"]["code"], "LOGIN_TAKEN")

    def test_bad_requests_never_echo_the_password(self):
        long_password = "p" * 129
        for response in (self.register(password="short-1"), self.register(login="12345"),
                         self.register(password=long_password), self.login(password=long_password),
                         self.register(role="admin")):
            with self.subTest(body=response.text[:80]):
                self.assertEqual(response.status_code, 422)
                self.assertNotIn("short-1", response.text)
                self.assertNotIn(long_password, response.text)
        with self.sessions() as session:
            self.assertEqual(session.execute(select(func.count()).select_from(User)).scalar(), 0)


class TokenTests(ApiCase):
    def refused(self, headers):
        response = self.client.get("/api/v1/auth/me", headers=headers)
        self.assertEqual(response.status_code, 401, headers)
        return response

    def test_tokens_that_are_not_ours(self):
        token = self.token()
        user_id = self.accounts.sign_in("9876543210", PASSWORD)["user_id"]
        header, payload, signature = token.split(".")
        now = datetime.now(timezone.utc)
        claims = {"sub": user_id, "ver": 0, "iat": now, "exp": now + timedelta(hours=1)}
        other_key, _ = create_access_token(user_id, 0, config(secret="another-secret-that-is-long-enough-xyz"))
        expired, _ = create_access_token(user_id, 0, config(), now=now - timedelta(days=1))
        for headers in ({"Authorization": token}, {"Authorization": f"Basic {token}"}, {"Authorization": "Bearer "},
                        self.bearer(f"{header}.{payload}.{signature[:-2]}xx"), self.bearer(other_key),
                        self.bearer(expired), self.bearer(jwt.encode(claims, None, algorithm="none"))):
            with self.subTest(headers=str(headers)[:40]):
                self.refused(headers)

    def test_a_token_ends_with_its_account(self):
        token = self.token()
        user_id = self.client.get("/api/v1/auth/me", headers=self.bearer(token)).json()["data"]["userId"]
        with self.sessions.begin() as session:
            session.delete(session.get(User, user_id))
        self.refused(self.bearer(token))

    def test_a_token_wins_over_the_dev_header(self):
        token = self.token()
        user_id = self.client.get("/api/v1/auth/me", headers=self.bearer(token)).json()["data"]["userId"]
        response = self.client.get("/api/v1/documents", headers={**self.bearer(token), "X-Actor-Id": "someone-else"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.documents.owners, [user_id])

    def test_the_dev_header_never_opens_the_account_database(self):
        def no_database():
            raise AssertionError("the account service was used")

        self.app.dependency_overrides[deps.account_lookup] = lambda: no_database
        response = self.client.get("/api/v1/documents", headers={"X-Actor-Id": "senior-meera"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.documents.owners, ["senior-meera"])
        self.assertEqual(self.client.get("/api/v1/documents").status_code, 401)


class ProductionTests(ApiCase):
    environment = "production"

    def test_a_token_is_needed_and_the_dev_header_is_refused(self):
        response = self.client.get("/api/v1/documents", headers={"X-Actor-Id": "senior-meera"})
        self.assertEqual(response.status_code, 401)
        self.assertIn("production", response.json()["error"]["message"])
        token = self.token()
        self.assertEqual(self.client.get("/api/v1/documents", headers=self.bearer(token)).status_code, 200)
        self.assertEqual(len(self.documents.owners), 1)


class PlaceholderSecretTests(ApiCase):
    environment = "production"
    secret = PLACEHOLDER

    def test_no_account_is_made_and_no_token_is_given(self):
        forged, _ = create_access_token("someone", 0, config(secret=PLACEHOLDER))  # anyone can make one
        with self.assertLogs("backend.api", level="ERROR") as logs:
            responses = (self.register(), self.login(),
                         self.client.get("/api/v1/auth/me", headers=self.bearer(forged)))
        for response in responses:
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["error"]["code"], "SIGN_IN_UNAVAILABLE")
            self.assertNotIn("JWT_SECRET", response.text)
            self.assertNotIn(PLACEHOLDER, response.text)
        self.assertEqual(len(logs.output), 3)
        self.assertTrue(all("JWT_SECRET is still the placeholder" in line for line in logs.output))  # for the operator
        self.assertTrue(all(PLACEHOLDER not in line for line in logs.output))
        with self.sessions() as session:
            self.assertEqual(session.execute(select(func.count()).select_from(User)).scalar(), 0)


if __name__ == "__main__":
    unittest.main()
