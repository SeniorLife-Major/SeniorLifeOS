"""Tests for step 7c-4: fixing look-alike characters (O/0, I/l/1, S/5, B/8...) in values."""

import json
import unittest
from unittest.mock import patch

from seniorlife_os.graphs.document_graph import build_document_graph
from seniorlife_os.tools.ocr_postprocess import fix_lookalikes, lookalike_key
from test_graph_ocr import AADHAAR, ocr_page, upload_state
from test_ocr import UNIT, ensure_fixtures
from test_privacy import FAKE_HASH_KEY, hash_key_env

CONFIRM = {"action": "confirm", "reviewer_id": "senior-meera"}


class FixLookalikesTests(unittest.TestCase):
    def test_swaps_only_where_the_shape_demands(self):
        cases = [
            ("ifsc", "DEM00206791", "DEMO0206791", ["0→O"]),          # 4th character must be a letter
            ("ifsc", "DEMOO206791", "DEMO0206791", ["O→0"]),          # 5th character must be zero
            ("pan_number", "HAVPC1B14K", "HAVPC1814K", ["B→8"]),
            ("pan_number", "HAVP5I814K", "HAVPS1814K", ["5→S", "I→1"]),  # 5th must be a letter, 6th a digit
            ("aadhaar_number", "3399 B698 7973", "3399 8698 7973", ["B→8"]),
            ("account_number", "44719O41229", "44719041229", ["O→0"]),
            ("annual_income", "Rs. 2,56,OOO/-", "Rs. 2,56,000/-", ["O→0", "O→0", "O→0"]),
            ("issue_date", "23/l2/2O26", "23/12/2026", ["l→1", "O→0"]),
            ("expiry_date", "2B-Aug-2029", "28-Aug-2029", ["B→8"]),   # the month name is left alone
            ("disability_percent", "4O%", "40%", ["O→0"]),
        ]
        for key, raw, fixed, changes in cases:
            with self.subTest(key=key, raw=raw):
                self.assertEqual(fix_lookalikes(key, raw), (fixed, changes))

    def test_nothing_to_change_gives_none(self):
        for key, raw in (("ifsc", "DEMO0206791"), ("aadhaar_number", AADHAAR), ("issue_date", "28-Aug-2029"),
                         ("ifsc", "DEMO020679"), ("pan_number", "SHORT")):
            with self.subTest(key=key, raw=raw):
                self.assertIsNone(fix_lookalikes(key, raw))

    def test_fields_without_a_fixed_shape_are_never_changed(self):
        for key, raw in (("name", "Suresh Kumar lyer"), ("policy_number", "HLT-2O26/OO4512"),
                         ("insurer", "Dem0 Health"), ("issuing_authority", "Medical B0ard")):
            with self.subTest(key=key):
                self.assertIsNone(fix_lookalikes(key, raw))

    def test_lookalike_key_folds_only_confusable_letters(self):
        self.assertEqual(lookalike_key("Suresh Kumar lyer"), lookalike_key("Suresh Kumar Iyer"))
        self.assertEqual(lookalike_key("R0HAN"), lookalike_key("Rohan"))
        self.assertNotEqual(lookalike_key("Meera Rao"), lookalike_key("Meena Rao"))


@patch("seniorlife_os.utils.privacy.find_env_file", return_value=None)
class GraphLookalikeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_fixtures()

    def run_upload(self, lines, doc_type, owner_name="Meera Rao", **extra):
        page = ocr_page([(text, 0.99) for text in lines])
        state = upload_state(UNIT / "sharp.png", "image/png", doc_type, **extra)
        state["document"]["owner_name"] = owner_name
        with patch("seniorlife_os.graphs.document_graph.read_document", return_value=[page]), hash_key_env(FAKE_HASH_KEY):
            return build_document_graph().invoke(state)

    PASSBOOK = ["Name: Meera Rao", "Bank: Demo Bank", "A/c No: 44719041229", "IFSC Code: DEM00206791"]

    def test_fixed_value_always_goes_to_review_even_when_ocr_was_sure(self, _):
        result = self.run_upload(self.PASSBOOK, "bank_passbook")
        self.assertEqual(result["status"], "needs_review")
        self.assertIn("ifsc: look-alike characters corrected (0→O), please check", result["review_flags"])
        self.assertEqual(result["normalized_fields"]["ifsc"], "DEMO0206791")
        self.assertEqual(result["extracted_fields"]["ifsc"]["corrections"], ["look-alike characters corrected (0→O)"])
        self.assertEqual(result["extracted_fields"]["ifsc"]["ocr_value"], "DEM00206791")

    def test_owner_confirms_the_fixed_value(self, _):
        result = self.run_upload(self.PASSBOOK, "bank_passbook", review_decision=CONFIRM)
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["vault_record"]["confirmed_fields"]["ifsc"]["value"], "DEMO0206791")

    def test_both_spellings_of_a_fixed_id_number_are_masked(self, _):
        misread = AADHAAR.replace("8", "B", 1)  # "3399 B698 7973"
        result = self.run_upload(["Name: Meera Rao", "Date of birth: 1950-09-17", f"Aadhaar: {misread}"], "identity",
                                 review_decision=CONFIRM)
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["vault_record"]["confirmed_fields"]["aadhaar_number"]["value"], "XXXX XXXX 7973")
        dumped = json.dumps(result, default=str)
        for spelling in (misread, AADHAAR, AADHAAR.replace(" ", "")):
            self.assertNotIn(spelling, dumped)

    def test_value_valid_as_read_is_never_touched(self, _):
        result = self.run_upload(["Name: Meera Rao", "Annual income: 22,66,000", "Issue date: 23/12/2025"],
                                 "income_certificate")
        self.assertNotIn("corrections", result["extracted_fields"]["annual_income"])
        self.assertEqual(result["normalized_fields"]["annual_income"], 2266000)

    def test_fix_that_still_fails_is_not_used(self, _):
        broken = "3399 B698 7974"  # after B→8 the checksum is still wrong
        result = self.run_upload(["Name: Meera Rao", "Date of birth: 1950-09-17", f"Aadhaar: {broken}"], "identity")
        self.assertEqual(result["status"], "needs_review")
        self.assertIn("aadhaar_number: failed format or checksum check", result["review_flags"])
        self.assertNotIn("corrections", result["extracted_fields"]["aadhaar_number"])
        self.assertFalse(any("look-alike" in flag for flag in result["review_flags"]))

    def test_name_is_explained_but_never_changed(self, _):
        result = self.run_upload(["Name: Suresh Kumar lyer", "Date of birth: 1950-09-17"], "identity",
                                 owner_name="Suresh Kumar Iyer")
        self.assertEqual(result["normalized_fields"]["name"], "Suresh Kumar lyer")
        self.assertIn("name differs from the provided owner name only by look-alike letters (I/l/1, O/0)",
                      result["review_flags"])

    def test_different_name_keeps_the_plain_flag(self, _):
        result = self.run_upload(["Name: Meena Rao", "Date of birth: 1950-09-17"], "identity")
        self.assertIn("name differs from the provided owner name", result["review_flags"])


if __name__ == "__main__":
    unittest.main()
