"""Limits on how often one person may do something costly or guessable (fastapi-backend Phase 5).

Each limit is a count in a fixed window, kept in the database (request_counts) so every server
shares it. count() adds one and says whether that was over the limit, in a single statement, so
two servers counting at the same moment can't both slip through.

The keys never hold a login (a phone number or e-mail): sign-in attempts are counted under a
keyed hash of it (sign_in_key).

Callers are the services: AccountService.sign_in, HealthService.start / answer and
DocumentService uploads. Limits are counted per login or per account, not per address: behind
nginx the server sees nginx's address for everyone (see Decision.md §14).
"""

from __future__ import annotations

import hashlib
import hmac
import math
import threading
import time
from datetime import timedelta

from sqlalchemy import case, delete, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .models import RequestCount

KEEP_ENDED_FOR = timedelta(days=1)  # windows that ended longer ago are removed
PURGE_EVERY_SECONDS = 3600  # per server

_purged_at: float | None = None
_purge_lock = threading.Lock()


def count(session: Session, key: str, limit: int, window: timedelta) -> int | None:
    """Count one more for key. None if it is within the limit, else the seconds until the
    window ends (it is not counted as allowed, and the window is not made longer)."""
    started_long_ago = RequestCount.window_start <= func.now() - window
    statement = (insert(RequestCount).values(key=key, window_start=func.now(), count=1)
                 .on_conflict_do_update(index_elements=[RequestCount.key], set_={
                     "count": case((started_long_ago, 1), else_=RequestCount.count + 1),
                     "window_start": case((started_long_ago, func.now()), else_=RequestCount.window_start)})
                 .returning(RequestCount.count, RequestCount.window_start, func.now()))
    counted, window_start, now = session.execute(statement).one()
    _purge_now_and_then(session)
    if counted <= limit:
        return None
    return max(1, math.ceil((window_start + window - now).total_seconds()))


def clear(session: Session, key: str) -> None:
    """Start counting again (e.g. after a right password)."""
    session.execute(delete(RequestCount).where(RequestCount.key == key))


def sign_in_key(login: str, secret: str) -> str:
    """The count key for sign-in attempts on a login, keyed with the token secret, so the
    stored key can't be turned back into the phone number or e-mail."""
    digest = hmac.new(secret.encode("utf-8"), b"sign-in-attempts\x00" + login.encode("utf-8"), hashlib.sha256)
    return "sign-in:" + digest.hexdigest()[:40]


def wait_words(seconds: int) -> str:
    minutes = math.ceil(seconds / 60)
    return "a minute" if minutes <= 1 else f"{minutes} minutes"


def _purge_now_and_then(session: Session) -> None:
    global _purged_at
    with _purge_lock:
        if _purged_at is not None and time.monotonic() - _purged_at < PURGE_EVERY_SECONDS:
            return
        _purged_at = time.monotonic()
    # Any window shorter than a day has ended once it started a day ago.
    session.execute(delete(RequestCount).where(RequestCount.window_start < func.now() - KEEP_ENDED_FOR))
