"""Tests for the step 6a document types: health insurance, bank passbook, disability certificate."""

import json
import unittest
from unittest.mock import patch

from backend.demo.demo_cases import disability_state, insurance_state, passbook_state
from backend.graphs.document_graph import build_document_graph
from backend.tools.document_rules import match_requirements
from backend.utils.privacy import keyed_hash
from test_privacy import FAKE_HASH_KEY, hash_key_env


def replace_line(state: dict, label: str, new_value: str) -> dict:
    page = state["document"]["pages"][0]
    lines = [f"{label}: {new_value}" if line.split(":", 1)[0] == label else line for line in page["text"].splitlines()]
    page["text"] = "\n".join(lines)
    return state


@patch("backend.utils.privacy.find_env_file", return_value=None)
class NewDocumentTypeTests(unittest.TestCase):
    def run_graph(self, state: dict) -> dict:
        with hash_key_env(FAKE_HASH_KEY):
            return build_document_graph().invoke(state)

    def test_health_insurance_is_confirmed_with_policy_masked_and_hashed(self, _):
        result = self.run_graph(insurance_state())
        self.assertEqual(result["status"], "confirmed")
        self.assertIn("classify_document:health_insurance", result["events"])
        fields = result["vault_record"]["confirmed_fields"]
        self.assertEqual(fields["sum_insured"]["value"], 500000)
        self.assertEqual(fields["policy_number"]["value"], "XXXXXXXXXXX4512")
        self.assertEqual(result["vault_record"]["expiry_date"], "2027-03-31")
        self.assertEqual(
            result["vault_record"]["identifier_hashes"]["policy_number"],
            keyed_hash("policy_number", "HLT-2026/004512", FAKE_HASH_KEY),
        )
        self.assertNotIn("HLT-2026/004512", json.dumps(result, default=str))

    def test_expired_insurance_goes_to_review(self, _):
        result = self.run_graph(replace_line(insurance_state(), "Valid till", "2026-01-31"))
        self.assertEqual(result["status"], "needs_review")
        self.assertIn("document is expired", result["review_flags"])
        self.assertNotIn("vault_record", result)

    def test_bank_passbook_masks_account_and_keeps_ifsc(self, _):
        result = self.run_graph(passbook_state())
        self.assertEqual(result["status"], "confirmed")
        fields = result["vault_record"]["confirmed_fields"]
        self.assertEqual(fields["account_number"]["value"], "XXXXXXXXXXX2345")
        self.assertEqual(fields["ifsc"]["value"], "DEMO0001234")
        self.assertIn("account_number", result["vault_record"]["identifier_hashes"])
        self.assertNotIn("123456789012345", json.dumps(result, default=str))

    def test_invalid_ifsc_goes_to_review(self, _):
        result = self.run_graph(replace_line(passbook_state(), "IFSC", "DEMO1001234"))
        self.assertEqual(result["status"], "needs_review")
        self.assertIn("ifsc: missing or invalid", result["review_flags"])

    def test_disability_certificate_percent_is_a_number(self, _):
        result = self.run_graph(disability_state())
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["vault_record"]["confirmed_fields"]["disability_percent"]["value"], 40)
        self.assertEqual(result["vault_record"]["identifier_hashes"], {})

    def test_disability_percent_over_100_goes_to_review(self, _):
        result = self.run_graph(replace_line(disability_state(), "Disability percentage", "150%"))
        self.assertEqual(result["status"], "needs_review")
        self.assertIn("disability_percent: missing or invalid", result["review_flags"])

    def test_passbook_without_hash_key_is_not_published(self, _):
        with hash_key_env(None):
            result = build_document_graph().invoke(passbook_state())
        self.assertEqual(result["status"], "failed_tool")
        self.assertNotIn("vault_record", result)

    def test_types_are_guessed_from_filename(self, _):
        for state, expected in ((insurance_state(), "health_insurance"), (passbook_state(), "bank_passbook"),
                                (disability_state(), "disability_certificate")):
            result = self.run_graph(state)
            self.assertIn(f"classify_document:{expected}", result["events"])

    def test_confirmed_records_satisfy_shared_requirements(self, _):
        records = [self.run_graph(make())["vault_record"] for make in (insurance_state, passbook_state, disability_state)]
        matches = match_requirements(
            ["health_insurance", "bank_passbook", "disability_certificate"], records, "senior-meera", "2026-09-22",
        )
        self.assertEqual([m["status"] for m in matches], ["usable", "usable", "usable"])


if __name__ == "__main__":
    unittest.main()
