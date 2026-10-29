"""Keep paid AI services (OpenAI, Groq, Google Vision) under a set number of requests per minute.

Each service has one shared limiter per process: all requests to it, from every thread, wait
their turn. Requests are spaced evenly (60 / per-minute seconds apart); the first goes at once.
This slows spending down and avoids "too many requests" answers; it does not cap the total
spent — set a spending limit on the provider's own dashboard for that.

The limit is read from a setting such as SENIORLIFE_LLM_MAX_PER_MINUTE (environment or .env).
"""

from __future__ import annotations

import asyncio
import os
import threading
import time

from langchain_core.rate_limiters import BaseRateLimiter

LLM_RATE_SETTING = "SENIORLIFE_LLM_MAX_PER_MINUTE"
CLOUD_RATE_SETTING = "SENIORLIFE_CLOUD_MAX_PER_MINUTE"
DEFAULT_LLM_PER_MINUTE = 30
DEFAULT_CLOUD_PER_MINUTE = 20


class SpacedRateLimiter(BaseRateLimiter):
    """At most `per_minute` requests a minute, spaced evenly. Usable as a LangChain chat model's
    `rate_limiter`, or directly with acquire()."""

    def __init__(self, per_minute: float) -> None:
        if per_minute <= 0:
            raise ValueError("per_minute must be above 0")
        self.per_minute = per_minute
        self.gap = 60.0 / per_minute
        self._next = 0.0  # time.monotonic() at which the next request may go
        self._lock = threading.Lock()

    def _reserve(self) -> float:
        """Book the next free slot; return how many seconds to wait for it."""
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next)
            self._next = start + self.gap
            return start - now

    def acquire(self, *, blocking: bool = True) -> bool:
        if not blocking:
            with self._lock:
                now = time.monotonic()
                if now < self._next:
                    return False
                self._next = now + self.gap
                return True
        wait = self._reserve()
        if wait > 0:
            time.sleep(wait)
        return True

    async def aacquire(self, *, blocking: bool = True) -> bool:
        if not blocking:
            return self.acquire(blocking=False)
        wait = self._reserve()
        if wait > 0:
            await asyncio.sleep(wait)
        return True


_limiters: dict[tuple[str, float], SpacedRateLimiter] = {}
_limiters_lock = threading.Lock()


def per_minute_setting(setting: str, default: float) -> float:
    """The requests-per-minute setting, or the default. Refuses a value that is not above 0."""
    raw = os.getenv(setting, "").strip()
    if not raw:
        return float(default)
    try:
        value = float(raw)
    except ValueError:
        raise RuntimeError(f"{setting} must be a number of requests per minute") from None
    if value <= 0:
        raise RuntimeError(f"{setting} must be above 0")
    return value


def rate_limiter(service: str, setting: str, default: float) -> SpacedRateLimiter:
    """The shared limiter of one service (e.g. "groq", "openai", "google_vision")."""
    per_minute = per_minute_setting(setting, default)
    with _limiters_lock:
        key = (service, per_minute)  # a changed setting gets a fresh limiter
        if key not in _limiters:
            _limiters[key] = SpacedRateLimiter(per_minute)
        return _limiters[key]
