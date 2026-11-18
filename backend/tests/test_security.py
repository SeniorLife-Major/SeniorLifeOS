"""Tests: passwords and sign-in tokens (core/security.py)."""

import base64
import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt

from backend.core.security import (
    InvalidToken,
    TokenSecretError,
    create_access_token,
    decode_access_token,
    hash_password,
    token_secret,
    verify_password,
)

SECRET = "a-test-secret-that-is-long-enough-0123456789"


def config(secret=SECRET, environment="development", ttl=720):
    return SimpleNamespace(app=SimpleNamespace(environment=environment),
                           security=SimpleNamespace(token_secret=secret, token_ttl_minutes=ttl))


class PasswordTests(unittest.TestCase):
    def test_right_and_wrong_password(self):
        stored = hash_password("correct horse battery")
        self.assertTrue(stored.startswith("scrypt$16384$8$1$"))
        self.assertNotIn("correct horse", stored)
        self.assertTrue(verify_password("correct horse battery", stored))
        self.assertFalse(verify_password("correct horse batterY", stored))
        self.assertFalse(verify_password("", stored))

    def test_each_hash_has_its_own_salt(self):
        self.assertNotEqual(hash_password("same password"), hash_password("same password"))

    def test_unicode_password(self):
        stored = hash_password("मेरा पासवर्ड 123")
        self.assertTrue(verify_password("मेरा पासवर्ड 123", stored))

    def test_stored_values_that_are_not_ours_never_match(self):
        good = hash_password("pw-12345678")
        salt, digest = good.split("$")[4:]
        for stored in ("", "plain-text-password", "bcrypt$x$y", f"scrypt$16384$8$1${salt}",
                       f"scrypt$1048576$8$1${salt}${digest}", f"scrypt$16384$8$1$zz${digest}", None):
            with self.subTest(stored=stored):
                self.assertFalse(verify_password("pw-12345678", stored))


class TokenTests(unittest.TestCase):
    def test_round_trip(self):
        token, seconds = create_access_token("user-1", 3, config())
        self.assertEqual(seconds, 720 * 60)
        self.assertEqual(decode_access_token(token, config()), {"sub": "user-1", "ver": 3})

    def test_expired(self):
        long_ago = datetime.now(timezone.utc) - timedelta(minutes=61)
        token, _ = create_access_token("user-1", 0, config(ttl=60), now=long_ago)
        with self.assertRaises(InvalidToken):
            decode_access_token(token, config())

    def test_another_secret_or_a_changed_token(self):
        token, _ = create_access_token("user-1", 0, config())
        with self.assertRaises(InvalidToken):
            decode_access_token(token, config(secret="another-secret-that-is-long-enough-xyz"))
        header, payload, signature = token.split(".")
        claims = json.loads(base64.urlsafe_b64decode(payload + "=="))
        claims["sub"] = "someone-else"
        forged = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
        with self.assertRaises(InvalidToken):
            decode_access_token(f"{header}.{forged}.{signature}", config())
        for junk in ("", "abc", "a.b.c"):
            with self.subTest(junk=junk), self.assertRaises(InvalidToken):
                decode_access_token(junk, config())

    def test_alg_none_and_other_algorithms_are_refused(self):
        now = datetime.now(timezone.utc)
        claims = {"sub": "user-1", "ver": 0, "iat": now, "exp": now + timedelta(hours=1)}
        unsigned = jwt.encode(claims, None, algorithm="none")
        with self.assertRaises(InvalidToken):
            decode_access_token(unsigned, config())
        other = jwt.encode(claims, SECRET, algorithm="HS512")
        with self.assertRaises(InvalidToken):
            decode_access_token(other, config())

    def test_missing_or_wrong_claims(self):
        now = datetime.now(timezone.utc)
        base = {"sub": "user-1", "ver": 0, "iat": now, "exp": now + timedelta(hours=1)}
        for name, change in (("no ver", {"ver": None}), ("text ver", {"ver": "0"}), ("bool ver", {"ver": True}),
                             ("no sub", {"sub": None}), ("no exp", {"exp": None})):
            claims = {key: value for key, value in {**base, **change}.items() if value is not None}
            with self.subTest(name), self.assertRaises(InvalidToken):
                decode_access_token(jwt.encode(claims, SECRET, algorithm="HS256"), config())


class SecretTests(unittest.TestCase):
    def test_short_secret_refused_everywhere(self):
        for environment in ("development", "production"):
            with self.subTest(environment=environment), self.assertRaises(TokenSecretError):
                token_secret(config(secret="short", environment=environment))

    def test_placeholder_refused_in_production_only(self):
        placeholder = "change-me-in-production-secret-key-32-chars-min"
        self.assertEqual(token_secret(config(secret=placeholder)), placeholder)
        for environment in ("production", "Prod"):
            with self.subTest(environment=environment), self.assertRaises(TokenSecretError) as caught:
                create_access_token("user-1", 0, config(secret=placeholder, environment=environment))
            self.assertNotIn(placeholder, str(caught.exception))

    def test_production_with_a_real_secret(self):
        token, _ = create_access_token("user-1", 0, config(environment="production"))
        self.assertEqual(decode_access_token(token, config(environment="production"))["sub"], "user-1")


if __name__ == "__main__":
    unittest.main()
