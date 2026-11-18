"""One ID per request, to find everything that happened for it (fastapi-backend Phase 5).

- RequestIdMiddleware gives each HTTP request an ID: the caller's X-Request-ID if it is a plain
  token (8-64 letters, digits, ".", "_", "-"), else a new random one. It is sent back in the
  X-Request-ID header and in every envelope's meta.requestId (success and error).
- RequestIdLogFilter puts it on every log record as %(request_id)s ("-" outside a request).
- in_this_request(fn) runs fn later, in another thread, under the same ID (doctor search's
  streaming worker, a background document reading).

The ID says nothing about the person; it only links the log lines of one request.
"""

from __future__ import annotations

import logging
import re
from contextvars import ContextVar, copy_context
from functools import partial
from typing import Any, Callable
from uuid import uuid4

HEADER = "X-Request-ID"
PLAIN_ID = re.compile(r"[A-Za-z0-9._-]{8,64}")

current_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def request_id() -> str | None:
    return current_request_id.get()


def in_this_request(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Callable[[], Any]:
    """fn(*args, **kwargs), to be run in another thread with this request's ID (and context)."""
    return partial(copy_context().run, fn, *args, **kwargs)


class RequestIdMiddleware:
    """Pure ASGI, so streamed responses (doctor search) keep the ID to the last event."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        sent = dict(scope.get("headers") or []).get(HEADER.lower().encode("latin-1"), b"").decode("latin-1")
        this_id = sent if PLAIN_ID.fullmatch(sent) else uuid4().hex
        # Not reset afterwards: an unexpected error is answered by Starlette's outermost handler,
        # after this middleware, and needs the ID too. Each request runs in its own task, and the
        # next request sets its own ID here first.
        current_request_id.set(this_id)

        async def send_with_id(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = [(name, value) for name, value in message.get("headers", [])
                           if name.lower() != HEADER.lower().encode("latin-1")]
                message = {**message, "headers": [*headers, (HEADER.lower().encode("latin-1"),
                                                             this_id.encode("latin-1"))]}
            await send(message)

        await self.app(scope, receive, send_with_id)


class RequestIdLogFilter(logging.Filter):
    """Adds record.request_id for the log format; never drops a record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = current_request_id.get() or "-"
        return True
