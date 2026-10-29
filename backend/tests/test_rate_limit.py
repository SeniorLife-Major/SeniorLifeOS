"""Tests: requests-per-minute limits on the paid AI services (utils/rate_limit.py) and the retry and
limit settings of the OpenAI and Groq chat models (agents/models.py). No request is sent.
"""

import asyncio
import os
import threading
import unittest
from unittest.mock import patch

from pydantic import BaseModel

from backend.agents import models
from backend.utils.rate_limit import (
    LLM_RATE_SETTING, SpacedRateLimiter, per_minute_setting, rate_limiter,
)


class Answer(BaseModel):
    name: str | None = None


class Clock:
    """Stands in for time.monotonic and time.sleep: sleeping moves the clock on."""

    def __init__(self):
        self.now, self.slept = 1000.0, []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(round(seconds, 6))
        self.now += seconds


def fake_clock():
    clock = Clock()
    return clock, patch.multiple("backend.utils.rate_limit.time", monotonic=clock.monotonic, sleep=clock.sleep)


class SpacedRateLimiterTests(unittest.TestCase):
    def test_first_request_goes_at_once_then_they_are_spaced(self):
        clock, patched = fake_clock()
        with patched:
            limiter = SpacedRateLimiter(per_minute=30)  # one every 2 seconds
            for _ in range(3):
                limiter.acquire()
        self.assertEqual(clock.slept, [2.0, 2.0])

    def test_no_wait_after_a_quiet_spell(self):
        clock, patched = fake_clock()
        with patched:
            limiter = SpacedRateLimiter(per_minute=30)
            limiter.acquire()
            clock.now += 10
            limiter.acquire()
        self.assertEqual(clock.slept, [])

    def test_without_blocking_it_says_no_instead_of_waiting(self):
        clock, patched = fake_clock()
        with patched:
            limiter = SpacedRateLimiter(per_minute=60)
            self.assertTrue(limiter.acquire(blocking=False))
            self.assertFalse(limiter.acquire(blocking=False))
            clock.now += 1
            self.assertTrue(limiter.acquire(blocking=False))
        self.assertEqual(clock.slept, [])

    def test_async_requests_wait_their_turn_too(self):
        clock, patched = fake_clock()
        waited = []

        async def fake_sleep(seconds):
            waited.append(seconds)

        with patched, patch("backend.utils.rate_limit.asyncio.sleep", fake_sleep):
            limiter = SpacedRateLimiter(per_minute=20)
            asyncio.run(limiter.aacquire())
            asyncio.run(limiter.aacquire())
        self.assertEqual(waited, [3.0])

    def test_threads_share_the_limit(self):
        limiter = SpacedRateLimiter(per_minute=60)
        waits, lock = [], threading.Lock()

        def take():
            wait = limiter._reserve()
            with lock:
                waits.append(round(wait))

        threads = [threading.Thread(target=take) for _ in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(waits), [0, 1, 2, 3, 4])  # each got its own slot

    def test_a_limit_must_be_above_zero(self):
        with self.assertRaises(ValueError):
            SpacedRateLimiter(per_minute=0)


class SettingTests(unittest.TestCase):
    def test_default_value_or_the_setting(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(per_minute_setting(LLM_RATE_SETTING, 30), 30.0)
        with patch.dict(os.environ, {LLM_RATE_SETTING: " 6 "}):
            self.assertEqual(per_minute_setting(LLM_RATE_SETTING, 30), 6.0)

    def test_bad_settings_are_refused(self):
        for value, message in (("many", "must be a number of requests per minute"), ("0", "must be above 0"),
                               ("-5", "must be above 0")):
            with self.subTest(value=value), patch.dict(os.environ, {LLM_RATE_SETTING: value}):
                with self.assertRaises(RuntimeError) as caught:
                    per_minute_setting(LLM_RATE_SETTING, 30)
                self.assertEqual(str(caught.exception), f"{LLM_RATE_SETTING} {message}")

    def test_one_limiter_per_service(self):
        with patch.dict(os.environ, {LLM_RATE_SETTING: "7"}):
            first = rate_limiter("test_service_a", LLM_RATE_SETTING, 30)
            self.assertIs(rate_limiter("test_service_a", LLM_RATE_SETTING, 30), first)
            self.assertIsNot(rate_limiter("test_service_b", LLM_RATE_SETTING, 30), first)
        self.assertEqual(first.per_minute, 7.0)


@patch("backend.agents.models.find_env_file", return_value=None)  # never the developer's own .env
class ChatModelLimitTests(unittest.TestCase):
    ENV = {"GROQ_API_KEY": "fake-groq-key", "OPENAI_API_KEY": "fake-openai-key", "SENIORLIFE_LLM_MODEL": "m",
           LLM_RATE_SETTING: "15"}

    def build(self, provider):
        with patch.dict(os.environ, {**self.ENV, "SENIORLIFE_LLM_PROVIDER": provider}, clear=True):
            return models.get_model()

    def test_both_providers_retry_twice_and_share_one_limit_each(self, _):
        for provider in ("groq", "openai"):
            with self.subTest(provider=provider):
                chat = self.build(provider)
                self.assertEqual(chat.max_retries, models.MAX_RETRIES)
                self.assertEqual(models.MAX_RETRIES, 2)
                self.assertIsInstance(chat.rate_limiter, SpacedRateLimiter)
                self.assertEqual(chat.rate_limiter.per_minute, 15.0)
                self.assertIs(self.build(provider).rate_limiter, chat.rate_limiter)  # shared across calls
        self.assertIsNot(self.build("groq").rate_limiter, self.build("openai").rate_limiter)

    def test_the_limit_is_kept_by_structured_output(self, _):
        # Every model call of the app goes through structured_model; LangChain waits on the
        # limiter inside each call.
        chat = self.build("groq")
        calls = []
        with patch.object(SpacedRateLimiter, "acquire", lambda self, blocking=True: calls.append(self) or True), \
                patch.object(type(chat), "_generate", side_effect=RuntimeError("no network in tests")):
            with patch.object(models, "get_model", return_value=chat), self.assertRaises(RuntimeError):
                models.structured_model(Answer).invoke("hello")
        self.assertEqual(calls, [chat.rate_limiter])


if __name__ == "__main__":
    unittest.main()
