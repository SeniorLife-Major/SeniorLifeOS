"""Tests for LangSmith tracing with private data hidden (utils/tracing.py).

No network: LangSmith's real Client is given a fake HTTP session that records
every upload instead of sending it.
"""

import contextlib
import json
import os
import unittest
from unittest.mock import patch

import langsmith
import requests

from backend.demo.demo_cases import document_success_state
from backend.graphs.document_graph import build_document_graph
from backend.utils import tracing
from backend.utils.tracing import HIDDEN, safe_view
from test_graph_ocr import AADHAAR, IDENTITY_LINES, ocr_page, upload_state
from test_ocr import DATASETS, UNIT, ensure_fixtures
from test_privacy import FAKE_HASH_KEY, hash_key_env

PRIVATE = ["Meera", "MEERA", "1950-09-17", AADHAAR, AADHAAR.replace(" ", ""), "1,20,000", "Demo District Office"]


class RecordingSession(requests.Session):
    """Answers every request with 200 and keeps the body that would have been sent."""

    def __init__(self):
        super().__init__()
        self.bodies = []

    def request(self, method, url, *args, **kwargs):
        body = kwargs.get("data") or kwargs.get("json") or b""
        if not isinstance(body, (bytes, str)):
            body = json.dumps(body, default=str)
        self.bodies.append(body if isinstance(body, bytes) else body.encode())
        response = requests.Response()
        response.status_code = 200
        response._content = b"{}"
        response.headers["Content-Type"] = "application/json"
        return response

    def sent_text(self):
        return b"".join(self.bodies).decode("utf-8", errors="ignore")


class SafeViewTests(unittest.TestCase):
    def test_allowlisted_keys_keep_values_and_everything_else_is_hidden(self):
        state = {
            "events": ["validate_upload", "read_file:fixture"], "status": "needs_review",
            "review_flags": ["name: low confidence (0.70)"], "actor_id": "senior-meera",
            "extracted_fields": {"name": {"value": "Meera Rao", "confidence": 0.7, "source_ref": "page:1:line:1"}},
            "normalized_fields": {"date_of_birth": "1950-09-17"},
        }
        view = safe_view(state)
        self.assertEqual(view["events"], state["events"])
        self.assertEqual(view["review_flags"], state["review_flags"])
        self.assertEqual(view["actor_id"], HIDDEN)
        self.assertEqual(view["extracted_fields"]["name"],
                         {"value": HIDDEN, "confidence": 0.7, "source_ref": "page:1:line:1"})
        self.assertEqual(view["normalized_fields"], {"date_of_birth": HIDDEN})

    def test_ocr_pages_keep_layout_facts_but_not_text(self):
        view = safe_view({"document": {"pages": [ocr_page(IDENTITY_LINES)], "owner_name": "Meera Rao"}})
        page = view["document"]["pages"][0]
        self.assertEqual(page["text"], HIDDEN)
        self.assertEqual(page["lines"][0]["text"], HIDDEN)
        self.assertEqual(page["lines"][0]["confidence"], 0.99)
        self.assertEqual(page["engine"], "fake")
        self.assertEqual(view["document"]["owner_name"], HIDDEN)

    def test_hash_maps_are_hidden_including_their_keys(self):
        view = safe_view({"vault_identifier_hashes": {"abc123hash": "doc-1"}, "identifier_hashes": {"pan_number": "f00"}})
        self.assertEqual(view, {"vault_identifier_hashes": HIDDEN, "identifier_hashes": HIDDEN})

    def test_llm_prompts_and_answers_are_hidden(self):
        run_input = {"messages": [[{"type": "human", "data": {"content": "Name: Meera Rao"}}]]}
        self.assertNotIn("Meera", json.dumps(safe_view(run_input)))
        self.assertNotIn("Meera", json.dumps(safe_view({"generations": [[{"text": "Meera Rao"}]]})))

    def test_id_numbers_in_kept_text_and_errors_are_redacted(self):
        view = safe_view({"errors": [f"Bad value {AADHAAR}"], "error": f"Traceback ... {AADHAAR}"})
        self.assertNotIn(AADHAAR, json.dumps(view))
        self.assertIn("[AADHAAR_1]", view["error"])

    def test_langgraph_metadata_is_kept(self):
        meta = {"langgraph_node": "read_file", "langgraph_step": 2, "thread_id": "t-1", "owner": "Meera"}
        self.assertEqual(safe_view(meta), {"langgraph_node": "read_file", "langgraph_step": 2,
                                           "thread_id": "t-1", "owner": HIDDEN})


@patch("backend.utils.privacy.find_env_file", return_value=None)
class TracedRunTests(unittest.TestCase):
    """Runs the graph with tracing on and inspects what LangSmith would upload."""

    @classmethod
    def setUpClass(cls):
        ensure_fixtures()

    def trace(self, state, anonymizer):
        session = RecordingSession()
        client = langsmith.Client(api_key="fake-key", api_url="https://example.invalid", session=session,
                                  auto_batch_tracing=False, anonymizer=anonymizer)
        with hash_key_env(FAKE_HASH_KEY), langsmith.tracing_context(enabled=True, client=client, project_name="test"):
            result = build_document_graph().invoke(state)
        return result, session.sent_text()

    def test_control_without_the_filter_the_private_data_would_be_sent(self, _):
        _, sent = self.trace(document_success_state(), anonymizer=None)
        self.assertIn("Meera", sent)  # proves the capture works, so the next tests mean something

    def test_fixture_run_sends_the_node_path_but_no_private_data(self, _):
        result, sent = self.trace(document_success_state(), anonymizer=safe_view)
        self.assertEqual(result["status"], "confirmed")
        for node in ("validate_upload", "read_file", "extract_content", "publish_confirmed_version"):
            self.assertIn(node, sent)
        for private in PRIVATE:
            self.assertNotIn(private, sent)

    def test_ocr_upload_run_sends_no_name_or_id_number(self, _):
        with patch("backend.graphs.document_graph.read_document", return_value=[ocr_page(IDENTITY_LINES)]):
            result, sent = self.trace(upload_state(UNIT / "sharp.png", "image/png", "identity"), anonymizer=safe_view)
        self.assertEqual(result["status"], "confirmed")
        self.assertIn("read_file:fake", sent)
        for private in PRIVATE:
            self.assertNotIn(private, sent)


@contextlib.contextmanager
def fake_langsmith():
    """Stand-ins for langsmith.Client and langsmith.configure: no real client, no network."""
    with patch("langsmith.Client") as client_class, patch("langsmith.configure") as configure:
        yield client_class, configure


class InstallTests(unittest.TestCase):
    def setUp(self):
        tracing._installed_for = None

    def tearDown(self):
        tracing._installed_for = None

    def assert_masked_client_installed(self, client_class, configure):
        client_class.assert_called_with(anonymizer=safe_view)
        configure.assert_called_with(client=client_class.return_value)

    def test_building_the_graph_installs_the_masked_client_when_tracing_is_on(self):
        for flag in tracing.FLAG_NAMES:  # new and old LangSmith names
            for key in tracing.KEY_NAMES:
                tracing._installed_for = None
                with self.subTest(flag=flag, key=key), patch.dict(os.environ, {flag: "true", key: "fake"}, clear=True), \
                        fake_langsmith() as (client_class, configure):
                    build_document_graph()
                    self.assert_masked_client_installed(client_class, configure)

    def test_nothing_is_installed_when_tracing_is_off(self):
        with patch.dict(os.environ, {}, clear=True), fake_langsmith() as (client_class, configure):
            build_document_graph()
            client_class.assert_not_called()
            configure.assert_not_called()

    def test_install_happens_once_per_api_key(self):
        with patch.dict(os.environ, {"LANGSMITH_TRACING": "true", "LANGSMITH_API_KEY": "fake-1"}), \
                fake_langsmith() as (_, configure):
            tracing.install_safe_client()
            tracing.install_safe_client()
            self.assertEqual(configure.call_count, 1)
            os.environ["LANGSMITH_API_KEY"] = "fake-2"  # e.g. .env loaded later with the real key
            tracing.install_safe_client()
            self.assertEqual(configure.call_count, 2)

    def test_loading_tracing_keys_from_any_env_file_reinstalls_the_client(self):
        from backend.utils.env import load_env_file

        env_file = DATASETS / "tracing_unit" / "key_only.env"
        env_file.parent.mkdir(parents=True, exist_ok=True)
        env_file.write_text("LANGSMITH_API_KEY=fake-key-loaded-late\n", encoding="utf-8")
        with patch.dict(os.environ, {"LANGSMITH_TRACING": "true"}, clear=True), \
                fake_langsmith() as (client_class, configure):
            load_env_file(env_file)
            self.assertEqual(configure.call_count, 1)
            self.assert_masked_client_installed(client_class, configure)

    def test_env_file_without_tracing_keys_does_not_touch_tracing(self):
        from backend.utils.env import load_env_file

        env_file = DATASETS / "tracing_unit" / "other.env"
        env_file.parent.mkdir(parents=True, exist_ok=True)
        env_file.write_text("SOMETHING_ELSE=1\n", encoding="utf-8")
        with patch.dict(os.environ, {}, clear=True), fake_langsmith() as (_, configure):
            load_env_file(env_file)
            configure.assert_not_called()

    def test_tracing_needs_both_the_flag_and_a_key(self):
        cases = [({"LANGSMITH_TRACING": "true", "LANGSMITH_API_KEY": "k"}, True),
                 ({"LANGSMITH_TRACING": "TRUE", "LANGSMITH_API_KEY": "k"}, True),
                 ({"LANGCHAIN_TRACING_V2": "true", "LANGSMITH_API_KEY": "k"}, True),
                 ({"LANGCHAIN_TRACING_V2": "true", "LANGCHAIN_API_KEY": "k"}, True),
                 ({"LANGSMITH_TRACING": "true", "LANGSMITH_API_KEY": "  "}, False),
                 ({"LANGSMITH_TRACING": "true"}, False),
                 ({"LANGSMITH_TRACING": "false", "LANGSMITH_API_KEY": "k"}, False),
                 ({}, False)]
        for env, expected in cases:
            with self.subTest(env=sorted(env)), patch.dict(os.environ, env, clear=True):
                self.assertEqual(tracing.tracing_enabled(), expected)

    def test_setup_reads_the_env_file_and_installs_the_client(self):
        env_file = DATASETS / "tracing_unit" / "fake.env"
        env_file.parent.mkdir(parents=True, exist_ok=True)
        env_file.write_text("LANGSMITH_TRACING=true\nLANGSMITH_API_KEY=fake-key-from-file\n", encoding="utf-8")
        with patch.dict(os.environ, {}, clear=True), fake_langsmith() as (client_class, configure), \
                patch("backend.utils.tracing.find_env_file", return_value=env_file):
            self.assertTrue(tracing.setup_tracing())
            self.assert_masked_client_installed(client_class, configure)


if __name__ == "__main__":
    unittest.main()
