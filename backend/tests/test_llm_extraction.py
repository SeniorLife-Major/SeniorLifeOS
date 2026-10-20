"""Tests for step 6d: the model fills required fields the rules could not find.

The model is replaced by a stand-in that records what it was sent, so no network is used.
"""

import json
import unittest
from unittest.mock import MagicMock, patch

from seniorlife_os.agents import models
from seniorlife_os.agents.llm_agents import extract_missing_fields, invoke_structured
from seniorlife_os.graphs.document_graph import build_document_graph
from test_graph_ocr import AADHAAR, ocr_page, upload_state
from test_ocr import UNIT, ensure_fixtures
from test_privacy import FAKE_HASH_KEY, hash_key_env

CERTIFICATE = [
    ("OFFICE OF THE TAHSILDAR (SAMPLE)", 0.99),
    ("INCOME CERTIFICATE", 0.99),
    ("This is to certify that Smt. Meera Rao, resident of Demo Nagar, has an", 0.98),
    ("annual income of Rs. 2,56,000/- (Rupees Two Lakh Fifty Six Thousand only).", 0.97),
    ("Place: Demo Taluka Date: 23/12/2025", 0.99),
]
CERTIFICATE_TEXT = "\n".join(text for text, _ in CERTIFICATE)


class FakeStructuredModel:
    """Stands in for structured_model(): records schemas and prompts, returns set answers."""

    def __init__(self, answers):
        self.answers = answers  # dict of field -> value, or an Exception to raise
        self.schemas, self.prompts, self.methods = [], [], []

    def __call__(self, schema, method=None):
        self.schemas.append(schema)
        self.methods.append(method)
        agent = MagicMock()

        def invoke(prompt):
            self.prompts.append(prompt)
            if isinstance(self.answers, Exception):
                raise self.answers
            return schema(**{k: v for k, v in self.answers.items() if k in schema.model_fields})

        agent.invoke.side_effect = invoke
        return agent


def run_with(fake, fn, *args, **kwargs):
    with patch("seniorlife_os.agents.llm_agents.structured_model", fake):
        return fn(*args, **kwargs)


class ExtractMissingFieldsTests(unittest.TestCase):
    def test_only_missing_fields_are_asked_for(self):
        fake = FakeStructuredModel({"name": "[NAME_1]"})
        run_with(fake, extract_missing_fields, CERTIFICATE_TEXT, "income_certificate", ["name", "annual_income"],
                 {"NAME": "Meera Rao"})
        self.assertEqual(set(fake.schemas[0].model_fields), {"name", "annual_income"})

    def test_prompt_is_redacted_and_placeholders_come_back_as_real_values(self):
        text = CERTIFICATE_TEXT + f"\nAadhaar {AADHAAR}"
        fake = FakeStructuredModel({"name": "[NAME_1]", "aadhaar_number": "[AADHAAR_1]"})
        found = run_with(fake, extract_missing_fields, text, "income_certificate", ["name", "aadhaar_number"],
                         {"NAME": "Meera Rao"})
        self.assertEqual(found, {"name": "Meera Rao", "aadhaar_number": AADHAAR})
        self.assertNotIn("Meera Rao", fake.prompts[0])
        self.assertNotIn(AADHAAR, fake.prompts[0])
        self.assertIn("Smt. [NAME_1]", fake.prompts[0])

    def test_values_not_copied_from_the_text_are_dropped(self):
        fake = FakeStructuredModel({"annual_income": "Rs. 3,00,000/-",  # invented
                                    "issue_date": "2025-12-23",          # reformatted, not as written
                                    "name": ""})                         # empty
        found = run_with(fake, extract_missing_fields, CERTIFICATE_TEXT, "income_certificate",
                         ["annual_income", "issue_date", "name"])
        self.assertEqual(found, {})

    def test_copied_values_are_kept_even_across_a_line_break(self):
        fake = FakeStructuredModel({"annual_income": "Rs. 2,56,000/-", "name": "Smt. [NAME_1], resident"})
        found = run_with(fake, extract_missing_fields, CERTIFICATE_TEXT, "income_certificate",
                         ["annual_income", "name"], {"NAME": "Meera Rao"})
        self.assertEqual(found, {"annual_income": "Rs. 2,56,000/-", "name": "Smt. Meera Rao, resident"})

    def test_model_is_not_called_when_redaction_fails(self):
        fake = FakeStructuredModel({})
        with patch("seniorlife_os.agents.llm_agents.redact", side_effect=RuntimeError("redaction broke")):
            with self.assertRaises(RuntimeError):
                run_with(fake, extract_missing_fields, CERTIFICATE_TEXT, "income_certificate", ["name"])
        self.assertEqual(fake.prompts, [])


class InvokeStructuredTests(unittest.TestCase):
    def test_skipped_tool_call_is_retried_as_json(self):
        from pydantic import BaseModel

        class Answer(BaseModel):
            name: str | None = None

        calls = []

        def factory(schema, method=None):
            calls.append(method)
            agent = MagicMock()
            if method is None:
                agent.invoke.side_effect = RuntimeError("Error code: 400 - {'code': 'tool_use_failed'}")
            else:
                agent.invoke.return_value = Answer(name="x")
            return agent

        with patch("seniorlife_os.agents.llm_agents.structured_model", factory):
            self.assertEqual(invoke_structured(Answer, "prompt").name, "x")
        self.assertEqual(calls, [None, "json_schema"])

    def test_other_errors_are_not_retried(self):
        factory = MagicMock()
        factory.return_value.invoke.side_effect = RuntimeError("rate limited")
        with patch("seniorlife_os.agents.llm_agents.structured_model", factory), self.assertRaises(RuntimeError):
            invoke_structured(object, "prompt")
        self.assertEqual(factory.call_count, 1)

    def test_structured_model_passes_an_explicit_method(self):
        fake_chat = MagicMock()
        with patch.object(models, "get_model", return_value=fake_chat), \
                patch.object(models, "current_provider", return_value="groq"):
            models.structured_model(dict)
            models.structured_model(dict, method="json_schema")
        self.assertEqual([c.kwargs["method"] for c in fake_chat.with_structured_output.call_args_list],
                         ["function_calling", "json_schema"])


@patch("seniorlife_os.utils.privacy.find_env_file", return_value=None)
class GraphLlmExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_fixtures()

    def run_upload(self, lines, fake, use_llm=True, **extra):
        page = ocr_page(lines)
        with patch("seniorlife_os.graphs.document_graph.read_document", return_value=[page]), \
                patch("seniorlife_os.agents.llm_agents.structured_model", fake), hash_key_env(FAKE_HASH_KEY):
            return build_document_graph().invoke(upload_state(UNIT / "sharp.png", "image/png", "income_certificate",
                                                              use_llm=use_llm, **extra))

    # The model sees the cleaned OCR text, where "Rs. " has become "₹" (step 7c-2).
    ANSWER = {"name": "[NAME_1]", "annual_income": "₹2,56,000/-"}

    def test_paragraph_certificate_fields_are_found_and_reviewed(self, _):
        fake = FakeStructuredModel(self.ANSWER)
        result = self.run_upload(CERTIFICATE, fake)
        self.assertIn("extract_content:missing_fields", result["events"])
        self.assertIn("llm_extract:found_2_of_2", result["events"])
        self.assertEqual(set(fake.schemas[0].model_fields), {"annual_income", "name"})  # issue date came from rules
        self.assertEqual(result["status"], "needs_review")
        for key in ("name", "annual_income"):
            self.assertIn(f"{key}: found by AI reading the text, please check", result["review_flags"])
        self.assertEqual(result["normalized_fields"]["annual_income"], 256000)
        self.assertEqual(result["extracted_fields"]["annual_income"]["source_ref"], "page:1:line:4")
        self.assertEqual(result["extracted_fields"]["annual_income"]["confidence"], 0.97)

    def test_owner_confirms_and_the_record_is_published(self, _):
        result = self.run_upload(CERTIFICATE, FakeStructuredModel(self.ANSWER),
                                 review_decision={"action": "confirm", "reviewer_id": "senior-meera"})
        self.assertEqual(result["status"], "confirmed")
        fields = result["vault_record"]["confirmed_fields"]
        self.assertEqual(fields["name"]["value"], "Meera Rao")
        self.assertEqual(fields["issue_date"]["value"], "2025-12-23")

    def test_model_failure_leaves_fields_missing_for_the_owner(self, _):
        result = self.run_upload(CERTIFICATE, FakeStructuredModel(RuntimeError("network down")))
        self.assertEqual(result["status"], "needs_review")
        self.assertIn("AI extraction unavailable (RuntimeError); missing fields need the owner", result["warnings"])
        self.assertIn("name: missing or invalid", result["review_flags"])
        self.assertNotIn("network down", json.dumps(result["warnings"]))

    def test_without_use_llm_the_model_is_never_called(self, _):
        fake = FakeStructuredModel(self.ANSWER)
        result = self.run_upload(CERTIFICATE, fake, use_llm=False)
        self.assertEqual(fake.prompts, [])
        self.assertNotIn("llm_extract", " ".join(result["events"]))

    def test_nothing_found_anywhere_asks_for_a_reupload(self, _):
        result = self.run_upload([("INCOME CERTIFICATE", 0.99), ("illegible", 0.6)], FakeStructuredModel({}))
        self.assertEqual(result["status"], "needs_reupload")
        self.assertIn("llm_extract:no_fields", result["events"])


if __name__ == "__main__":
    unittest.main()
