"""Things several routers need (fastapi-backend session): is this a production server, the
account service, and who is calling (current_actor)."""

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
           "get_account_service", "account_lookup"]

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


# ---- the account service ------------------------------------------------------------------

_accounts: Any = None
_accounts_lock = threading.Lock()


def get_account_service() -> Any:
    """The shared AccountService, made on first use from DATABASE_URL."""
    global _accounts
    with _accounts_lock:
        if _accounts is None:
            _accounts = build_account_service()
        return _accounts


def build_account_service() -> Any:
    from sqlalchemy import inspect

    from backend.storage.accounts import AccountService
    from backend.storage.db import make_engine, make_sessions
    from backend.storage.settings import StorageSettingError, database_url

    pool = get_config().database
    try:
        engine = make_engine(database_url(), pool_size=pool.pool_size, max_overflow=pool.max_overflow, echo=pool.echo)
        if not inspect(engine).has_table("users"):
            engine.dispose()
            raise StorageUnavailable("The account tables are missing; run: alembic -c backend/alembic.ini upgrade head")
    except StorageSettingError as exc:
        raise StorageUnavailable(str(exc)) from None  # names the setting, never its value
    except StorageUnavailable:
        raise
    except Exception as exc:
        logger.error("Account storage could not start: %s", type(exc).__name__)
        raise StorageUnavailable("The account database can't be reached") from None
    return AccountService(make_sessions(engine))


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
