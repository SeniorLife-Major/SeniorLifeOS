"""Things several routers need (fastapi-backend session): is this a production server, the
account and doctor search services, and who is calling (current_actor)."""

from __future__ import annotations

import logging
import re
import threading
from typing import Annotated, Any, Callable

from fastapi import Depends, Header

from backend.core.config import PRODUCTION, get_config, is_production
from backend.core.security import InvalidToken, TokenSecretError, decode_access_token
from backend.errors import AppException, UnauthorizedException
from backend.errors.error_codes import ErrorCode

__all__ = ["PRODUCTION", "is_production", "StorageUnavailable", "SignInUnavailable", "current_actor", "Actor",
           "get_account_service", "get_health_service", "account_lookup"]

logger = logging.getLogger(__name__)

ACTOR_ID = re.compile(r"[A-Za-z0-9._:@-]{1,128}")
SIGN_IN_AGAIN = "Your sign-in has ended or is not valid; please sign in again"


class StorageUnavailable(AppException):
    def __init__(self, message: str) -> None:
        super().__init__(message=message, error_code="STORAGE_UNAVAILABLE", status_code=503)


class SignInUnavailable(AppException):
    """The server's token secret is unusable. The details go to the log, not to the caller."""

    def __init__(self) -> None:
        super().__init__(message="Sign-in is not available right now", error_code=ErrorCode.SIGN_IN_UNAVAILABLE,
                         status_code=503)


# ---- services made here: accounts, doctor search ------------------------------------------
# They share one connection pool (DATABASE_URL). The document service still makes its own.

_lock = threading.RLock()  # re-entrant: building one service builds the shared sessions first
_made: dict[str, Any] = {}


def shared(name: str, build: Callable[[], Any]) -> Any:
    """The one instance of name, made on first use. A failed build is tried again next time."""
    with _lock:
        if name not in _made:
            _made[name] = build()
        return _made[name]


def database_sessions() -> Any:
    def build() -> Any:
        from backend.storage.db import make_engine, make_sessions
        from backend.storage.settings import StorageSettingError, database_url

        pool = get_config().database
        try:
            url = database_url()
        except StorageSettingError as exc:
            raise StorageUnavailable(str(exc)) from None  # names the setting, never its value
        return make_sessions(make_engine(url, pool_size=pool.pool_size, max_overflow=pool.max_overflow,
                                         echo=pool.echo))

    return shared("sessions", build)


def needs_table(sessions: Any, table: str, what: str) -> None:
    from sqlalchemy import inspect

    try:
        present = inspect(sessions.kw["bind"]).has_table(table)
    except Exception as exc:
        logger.error("%s storage could not start: %s", what.capitalize(), type(exc).__name__)
        raise StorageUnavailable(f"The {what} database can't be reached") from None
    if not present:
        raise StorageUnavailable(f"The {what} tables are missing; run: alembic -c backend/alembic.ini upgrade head")


def get_account_service() -> Any:
    """The shared AccountService."""
    return shared("accounts", build_account_service)


def build_account_service() -> Any:
    from backend.storage.accounts import AccountService

    sessions = database_sessions()
    needs_table(sessions, "users", "account")
    return AccountService(sessions)


def get_health_service() -> Any:
    """The shared HealthService (doctor search); also needs SENIORLIFE_CHECKPOINT_KEY."""
    return shared("health", build_health_service)


def build_health_service() -> Any:
    from backend.storage.health_service import HealthService
    from backend.storage.settings import database_url, with_driver
    from backend.utils.checkpoints import CheckpointKeyError, make_postgres_checkpointer

    sessions = database_sessions()
    needs_table(sessions, "graph_runs", "doctor search")
    try:
        checkpointer = make_postgres_checkpointer(with_driver(database_url(), ""))
    except CheckpointKeyError as exc:
        raise StorageUnavailable(str(exc)) from None  # names the setting, never its value
    except Exception as exc:
        logger.error("Doctor search storage could not start: %s", type(exc).__name__)
        raise StorageUnavailable("The doctor search database can't be reached") from None
    return HealthService(sessions, checkpointer)


def account_lookup() -> Callable[[], Any]:
    """How current_actor reaches the account service: only when a token is sent, so development
    calls with X-Actor-Id never touch the database. Tests override this one."""
    return get_account_service


# ---- who is calling -----------------------------------------------------------------------

def current_actor(
    accounts: Annotated[Callable[[], Any], Depends(account_lookup)],
    authorization: Annotated[str | None, Header()] = None,
    x_actor_id: Annotated[str | None, Header()] = None,
) -> str:
    """The caller's user id.
    - Authorization: Bearer <token> (from /api/v1/auth/login): the token must be signed with
      JWT_SECRET, not expired, and the account must still exist, be switched on and not have
      signed out since.
    - Without a token, in development only: the X-Actor-Id header, as before sign-in existed."""
    config = get_config()
    if authorization:
        scheme, _, token = authorization.strip().partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise UnauthorizedException("Send the sign-in token as: Authorization: Bearer <token>")
        try:
            claims = decode_access_token(token.strip(), config)
        except TokenSecretError as exc:
            logger.error("Sign-in tokens can't be checked: %s", exc)
            raise SignInUnavailable() from None
        except InvalidToken:
            raise UnauthorizedException(SIGN_IN_AGAIN) from None
        if not accounts().token_valid(claims["sub"], claims["ver"]):
            raise UnauthorizedException(SIGN_IN_AGAIN)
        return claims["sub"]
    if is_production(config):
        raise UnauthorizedException("Please sign in first; the X-Actor-Id header is not accepted in production")
    if not x_actor_id or not ACTOR_ID.fullmatch(x_actor_id):
        raise UnauthorizedException("Please sign in first (in development the X-Actor-Id header also works)")
    return x_actor_id


Actor = Annotated[str, Depends(current_actor)]
