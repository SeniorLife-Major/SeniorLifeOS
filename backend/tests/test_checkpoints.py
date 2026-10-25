"""Tests for encrypted checkpoints (utils/checkpoints.py)."""

import os
import unittest
from unittest.mock import patch

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from backend.demo.demo_cases import document_success_state
from backend.graphs import document_graph
from backend.graphs.document_graph import build_document_graph
from backend.utils import checkpoints
from backend.utils.checkpoints import (
    KEY_NAME, CheckpointKeyError, StrictEncryptedSerializer, checkpoint_key, make_checkpointer, run_and_forget,
)
from test_privacy import FAKE_HASH_KEY, fake_aadhaar, hash_key_env

KEY = bytes(range(32))
OTHER_KEY = bytes(range(1, 33))
OWNER = "senior-meera"
CONFIRM = {"action": "confirm", "reviewer_id": OWNER, "edits": {"issue_date": "2026-06-01"}}


def paused_state(aadhaar):
    state = document_success_state()
    state.pop("review_decision")
    state["use_interrupt"] = True
    state["document"]["pages"][0]["text"] += f"\nAadhaar: {aadhaar}"
    return state


def raw_bytes(saver):
    """Every bytes value the checkpointer keeps, however deeply it is stored."""
    found = []

    def walk(value):
        if isinstance(value, (bytes, bytearray)):
            found.append(bytes(value))
        elif isinstance(value, dict):
            for key, item in value.items():
                walk(key)
                walk(item)
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                walk(item)
        elif isinstance(value, str):
            found.append(value.encode())

    walk(saver.storage)
    walk(saver.writes)
    walk(saver.blobs)
    return b"\n".join(found)


@patch("backend.utils.privacy.find_env_file", return_value=None)
class EncryptedCheckpointTests(unittest.TestCase):
    def pause(self, saver, thread="t1", aadhaar=None):
        aadhaar = aadhaar or fake_aadhaar(30)
        with hash_key_env(FAKE_HASH_KEY):
            graph = build_document_graph(checkpointer=saver)
            pending = graph.invoke(paused_state(aadhaar), {"configurable": {"thread_id": thread}})
        self.assertIn("__interrupt__", pending)
        return graph, aadhaar

    def test_nothing_personal_is_readable_in_what_is_saved(self, _):
        saver = make_checkpointer(KEY)
        _, aadhaar = self.pause(saver)
        saved = raw_bytes(saver)
        self.assertTrue(saved)
        for secret in ("Meera Rao", aadhaar, aadhaar.replace(" ", ""), "Annual income"):
            self.assertNotIn(secret.encode(), saved)

    def test_control_a_plain_checkpointer_would_show_them(self, _):
        # Proves the test above can see plain text: without encryption the name is right there.
        saver = InMemorySaver()
        with patch.object(document_graph, "is_encrypted", return_value=True):
            _, aadhaar = self.pause(saver)
        self.assertIn(b"Meera Rao", raw_bytes(saver))
        self.assertIn(aadhaar.encode(), raw_bytes(saver))

    def test_resume_with_the_same_key(self, _):
        graph, _ = self.pause(make_checkpointer(KEY))
        with hash_key_env(FAKE_HASH_KEY):
            finished = graph.invoke(Command(resume=CONFIRM), {"configurable": {"thread_id": "t1"}})
        self.assertEqual(finished["status"], "confirmed")

    def test_another_key_cannot_resume(self, _):
        first = make_checkpointer(KEY)
        self.pause(first)
        second = make_checkpointer(OTHER_KEY)
        second.storage, second.writes, second.blobs = first.storage, first.writes, first.blobs
        with hash_key_env(FAKE_HASH_KEY), self.assertRaises(ValueError):  # the tamper check fails
            build_document_graph(checkpointer=second).invoke(Command(resume=CONFIRM),
                                                             {"configurable": {"thread_id": "t1"}})

    def test_a_plain_checkpointer_is_refused(self, _):
        with self.assertRaises(ValueError):
            build_document_graph(checkpointer=InMemorySaver())

    def test_runs_that_end_leave_nothing_behind(self, _):
        saver = make_checkpointer(KEY)
        config = {"configurable": {"thread_id": "t2"}}
        with hash_key_env(FAKE_HASH_KEY):
            graph = build_document_graph(checkpointer=saver)
            pending = run_and_forget(graph, paused_state(fake_aadhaar(31)), config)
            self.assertIn("__interrupt__", pending)
            self.assertIn("t2", saver.storage)  # still paused: kept for the resume
            finished = run_and_forget(graph, Command(resume=CONFIRM), config)
        self.assertEqual(finished["status"], "confirmed")
        self.assertNotIn("t2", saver.storage)
        self.assertFalse([key for key in saver.writes if key[0] == "t2"])
        self.assertFalse([key for key in saver.blobs if key[0] == "t2"])


class SerializerTests(unittest.TestCase):
    def test_unencrypted_data_is_refused(self):
        serde = checkpoints.encrypted_serde(KEY)
        plain = InMemorySaver().serde.dumps_typed({"name": "Meera Rao"})
        with self.assertRaises(ValueError):
            serde.loads_typed(plain)

    def test_round_trip_and_tamper_check(self):
        serde = checkpoints.encrypted_serde(KEY)
        kind, data = serde.dumps_typed({"name": "Meera Rao"})
        self.assertEqual(serde.loads_typed((kind, data)), {"name": "Meera Rao"})
        tampered = data[:-1] + bytes([data[-1] ^ 1])
        with self.assertRaises(ValueError):
            serde.loads_typed((kind, tampered))
        self.assertIsInstance(serde, StrictEncryptedSerializer)


@patch("backend.utils.checkpoints.find_env_file", return_value=None)
class KeyTests(unittest.TestCase):
    def with_key(self, value):
        env = {k: v for k, v in os.environ.items() if k != KEY_NAME}
        if value is not None:
            env[KEY_NAME] = value
        return patch.dict(os.environ, env, clear=True)

    def test_key_is_read_from_the_environment(self, _):
        with self.with_key(KEY.hex()):
            self.assertEqual(checkpoint_key(), KEY)
            self.assertIsInstance(make_checkpointer().serde, StrictEncryptedSerializer)

    def test_missing_or_malformed_key_stops_and_never_shows_the_value(self, _):
        for value in (None, "", "short", "zz" * 32, KEY.hex() + "00"):
            with self.subTest(value=value), self.with_key(value):
                with self.assertRaises(CheckpointKeyError) as caught:
                    make_checkpointer()
                if value:
                    self.assertNotIn(value, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
