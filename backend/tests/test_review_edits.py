"""Tests: during review the owner may fill in a required field OCR missed (e.g. a name
printed without a label), but not add any other field."""

import json
import unittest
from unittest.mock import patch

from langgraph.types import Command

from backend.graphs.document_graph import build_document_graph
from backend.tools.document_rules import match_requirements
from backend.utils.checkpoints import make_checkpointer
from test_graph_ocr import AADHAAR, ocr_page, upload_state
from test_ocr import UNIT, ensure_fixtures
from test_privacy import FAKE_HASH_KEY, hash_key_env

OWNER = "senior-meera"
# Like the hard ID card: the name is printed with no label, so it is not extracted.
UNLABELLED_NAME = [("SAMPLE IDENTITY CARD", 0.99), ("Meera Rao", 0.99), ("DOB: 17/09/1950", 0.99),
                   (f"Aadhaar: {AADHAAR}", 0.99)]
PASSBOOK_WITHOUT_ACCOUNT = [("Name: Meera Rao", 0.99), ("Bank: State Bank of India", 0.99),
                            ("IFSC: SBIN0001234", 0.99)]


@patch("backend.utils.privacy.find_env_file", return_value=None)
class OwnerFillsInMissingFieldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_fixtures()

    def run_upload(self, lines, doc_type="identity", **extra):
        with patch("backend.graphs.document_graph.read_document", return_value=[ocr_page(lines)]), \
                hash_key_env(FAKE_HASH_KEY):
            return build_document_graph().invoke(upload_state(UNIT / "sharp.png", "image/png", doc_type, **extra))

    def confirm(self, lines, edits, doc_type="identity"):
        return self.run_upload(lines, doc_type, review_decision={"action": "confirm", "reviewer_id": OWNER,
                                                                 "edits": edits})

    def test_without_the_name_the_card_waits_for_review(self, _):
        result = self.run_upload(UNLABELLED_NAME)
        self.assertEqual(result["status"], "needs_review")
        self.assertIn("name: missing or invalid", result["review_flags"])
        self.assertNotIn("name", result["extracted_fields"])

    def test_the_owner_fills_in_the_missing_name(self, _):
        result = self.confirm(UNLABELLED_NAME, {"name": "Meera Rao"})
        self.assertEqual(result["status"], "confirmed")
        name = result["vault_record"]["confirmed_fields"]["name"]
        self.assertEqual(name["value"], "Meera Rao")
        self.assertEqual(name["source_ref"], "document:doc-upload-001:entered_by_owner")
        self.assertTrue(name["corrected_by_user"])
        self.assertEqual(name["reviewed_by"], OWNER)
        dob = result["vault_record"]["confirmed_fields"]["date_of_birth"]
        self.assertIn(":page:1:line:", dob["source_ref"])  # read values keep their place on the page
        self.assertFalse(dob["corrected_by_user"])
        self.assertNotIn(AADHAAR, json.dumps(result, default=str))

    def test_the_filled_in_record_is_accepted_downstream(self, _):
        record = self.confirm(UNLABELLED_NAME, {"name": "Meera Rao"})["vault_record"]
        matches = match_requirements(["identity"], [record], OWNER, "2026-09-22")
        self.assertEqual(matches[0]["status"], "usable")

    def test_a_field_that_is_neither_read_nor_required_is_refused(self, _):
        result = self.confirm(UNLABELLED_NAME, {"name": "Meera Rao", "annual_income": "5,00,000"})
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["errors"],
                         ["Edits may only correct extracted fields or fill in this document's required or optional fields"])
        self.assertNotIn("vault_record", result)

    def test_the_owner_adds_an_optional_id_number_ocr_missed(self, _):
        lines = [line for line in UNLABELLED_NAME if not line[0].startswith("Aadhaar")]  # no Aadhaar read
        result = self.confirm(lines, {"name": "Meera Rao", "aadhaar_number": AADHAAR})
        self.assertEqual(result["status"], "confirmed")
        field = result["vault_record"]["confirmed_fields"]["aadhaar_number"]
        self.assertEqual(field["value"], "XXXX XXXX 7973")
        self.assertEqual(field["source_ref"], "document:doc-upload-001:entered_by_owner")
        self.assertIn("aadhaar_number", result["identifier_hashes"])
        self.assertNotIn(AADHAAR.replace(" ", ""), json.dumps(result, default=str))

    def test_an_added_optional_field_is_checked_like_a_read_one(self, _):
        lines = [line for line in UNLABELLED_NAME if not line[0].startswith("Aadhaar")]
        wrong = AADHAAR[:-1] + "4"  # one digit off: fails the check digit
        result = self.confirm(lines, {"name": "Meera Rao", "aadhaar_number": wrong})
        self.assertEqual(result["status"], "needs_review")  # refused, not silently dropped
        self.assertEqual(result["errors"], ["Correct these fields: aadhaar_number"])
        self.assertNotIn("vault_record", result)

    def test_optional_fields_of_another_type_are_still_refused(self, _):
        result = self.confirm(PASSBOOK_WITHOUT_ACCOUNT, {"account_number": "44719041229", "aadhaar_number": AADHAAR},
                              doc_type="bank_passbook")
        self.assertEqual(result["status"], "needs_review")
        self.assertIn("required or optional fields", result["errors"][0])

    def test_a_filled_in_value_is_checked_like_a_read_one(self, _):
        lines = [line for line in UNLABELLED_NAME if not line[0].startswith("DOB")]
        result = self.confirm(lines, {"name": "Meera Rao", "date_of_birth": "sometime in 1950"})
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["errors"], ["Correct these required fields: date_of_birth"])
        result = self.confirm(lines, {"name": "Meera Rao", "date_of_birth": "17/09/1950"})
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["vault_record"]["confirmed_fields"]["date_of_birth"]["value"], "1950-09-17")

    def test_a_filled_in_id_number_is_masked_and_hashed(self, _):
        account = "44719041229"
        result = self.confirm(PASSBOOK_WITHOUT_ACCOUNT, {"account_number": account}, doc_type="bank_passbook")
        self.assertEqual(result["status"], "confirmed")
        field = result["vault_record"]["confirmed_fields"]["account_number"]
        self.assertEqual(field["value"], "XXXXXXX1229")
        self.assertEqual(field["source_ref"], "document:doc-upload-001:entered_by_owner")
        self.assertIn("account_number", result["identifier_hashes"])
        self.assertNotIn(account, json.dumps(result, default=str))

    def test_a_filled_in_id_number_is_masked_when_the_run_stops_too(self, _):
        account = "44719041229"
        result = self.confirm(PASSBOOK_WITHOUT_ACCOUNT, {"account_number": account, "ifsc": "not an ifsc"},
                              doc_type="bank_passbook")
        self.assertEqual(result["status"], "needs_review")  # the IFSC is wrong; the owner tries again
        self.assertEqual(result["review_decision"]["edits"]["account_number"], account)  # kept for the owner
        rejected = self.run_upload(PASSBOOK_WITHOUT_ACCOUNT, "bank_passbook", review_decision={
            "action": "reject", "reviewer_id": OWNER, "edits": {"account_number": account}})
        self.assertEqual(rejected["status"], "rejected")
        self.assertNotIn(account, json.dumps(rejected, default=str))

    def test_the_pause_tells_the_owner_what_is_missing(self, _):
        graph = build_document_graph(checkpointer=make_checkpointer(key=bytes(range(32))))
        config = {"configurable": {"thread_id": "fill-in"}}
        with patch("backend.graphs.document_graph.read_document", return_value=[ocr_page(UNLABELLED_NAME)]), \
                hash_key_env(FAKE_HASH_KEY):
            paused = graph.invoke(upload_state(UNIT / "sharp.png", "image/png", "identity", use_interrupt=True),
                                  config)
            self.assertEqual(paused["__interrupt__"][0].value["missing_fields"], ["name"])
            self.assertEqual(paused["__interrupt__"][0].value["optional_fields"], ["pan_number"])  # Aadhaar was read
            finished = graph.invoke(Command(resume={"action": "confirm", "reviewer_id": OWNER,
                                                    "edits": {"name": "Meera Rao"}}), config)
        self.assertEqual(finished["status"], "confirmed")


if __name__ == "__main__":
    unittest.main()
