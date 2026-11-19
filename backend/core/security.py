"""Passwords and sign-in tokens (fastapi-backend Phase 1).

Passwords: scrypt from the standard library (memory-hard, so guessing is slow even on a GPU), a
random 16-byte salt per password, stored as "scrypt$n$r$p$<salt hex>$<hash hex>" and compared in
constant time. The password itself is never stored or logged.

Tokens: a JWT signed with HS256 (PyJWT) using security.token_secret (JWT_SECRET). Claims:
- sub: the user id;
- ver: users.token_version at sign-in. Signing out adds 1, so every older token stops working;
- iat, exp: issued and expiry times (security.token_ttl_minutes, default 12 hours).
Only HS256 is accepted, so a token claiming "alg": "none" or another algorithm is refused.
Production refuses a placeholder or short secret (anyone could sign tokens with it).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from .config import is_production

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1  # about 16 MB of memory and ~50 ms per check
SALT_BYTES = 16
HASH_BYTES = 32
ALGORITHM = "HS256"
MIN_SECRET_CHARS = 32
PLACEHOLDER_PREFIX = "change-me"  # the defaults in application.yml and .env.example


class TokenSecretError(RuntimeError):
    """The token secret is unusable here. The message names the setting, never its value."""


class InvalidToken(Exception):
    """Expired, tampered with, signed with another key, or missing a claim."""


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=HASH_BYTES,
                          maxmem=256 * r * n)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(SALT_BYTES)
    digest = _scrypt(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """False for a wrong password or a stored value that is not one of ours."""
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        n, r, p = int(n), int(r), int(p)
        if not (2 ** 10 <= n <= 2 ** 16 and 1 <= r <= 16 and 1 <= p <= 4):  # never a huge memory use
            return False
        expected = bytes.fromhex(digest)
        actual = _scrypt(password, bytes.fromhex(salt), n, r, p)
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(actual, expected)


_DUMMY_HASH: str | None = None


def burn_password_check(password: str) -> None:
    """Spend the same time as a real check, for a login that matches no account: the answer
    time then does not tell whether an account exists."""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password(secrets.token_hex(8))
    verify_password(password, _DUMMY_HASH)


def token_secret(config: Any) -> str:
    secret = config.security.token_secret or ""
    if len(secret) < MIN_SECRET_CHARS:
        raise TokenSecretError(f"JWT_SECRET must be at least {MIN_SECRET_CHARS} characters")
    if is_production(config) and secret.startswith(PLACEHOLDER_PREFIX):
        raise TokenSecretError("JWT_SECRET is still the placeholder; set a random value in production")
    return secret


def create_access_token(user_id: str, token_version: int, config: Any,
                        now: datetime | None = None) -> tuple[str, int]:
    """(token, seconds until it expires)."""
    issued = now or datetime.now(timezone.utc)
    lifetime = int(config.security.token_ttl_minutes) * 60
    claims = {"sub": user_id, "ver": token_version, "iat": issued, "exp": issued + timedelta(seconds=lifetime)}
    return jwt.encode(claims, token_secret(config), algorithm=ALGORITHM), lifetime


def decode_access_token(token: str, config: Any) -> dict[str, Any]:
    """{"sub": user id, "ver": token version}, or InvalidToken. TokenSecretError if the server's
    own secret is unusable (a server problem, not the caller's)."""
    secret = token_secret(config)
    try:
        claims = jwt.decode(token, secret, algorithms=[ALGORITHM],
                            options={"require": ["sub", "ver", "iat", "exp"]})
    except jwt.PyJWTError:
        raise InvalidToken() from None
    if not isinstance(claims.get("sub"), str) or not claims["sub"] or type(claims.get("ver")) is not int:
        raise InvalidToken()
    return {"sub": claims["sub"], "ver": claims["ver"]}
