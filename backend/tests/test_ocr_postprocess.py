"""Tests for step 7c-2: OCR text cleanup and forgiving label matching."""

import json
import unittest
from unittest.mock import patch

from seniorlife_os.graphs.document_graph import build_document_graph
from seniorlife_os.tools.demo_tools import extract_fields_from_pages, field_for_label
from seniorlife_os.tools.ocr_postprocess import clean_line, clean_page
from test_graph_ocr import ocr_page, upload_state
from test_ocr import UNIT, ensure_fixtures
from test_privacy import FAKE_HASH_KEY, hash_key_env


class CleanLineTests(unittest.TestCase):
    def assert_clean(self, raw, expected, rules):
        self.assertEqual(clean_line(raw), (expected, rules))

    def test_full_width_characters(self):
        self.assert_clean("महिला ／ FEMALE", "महिला / FEMALE", ["nfkc"])
        self.assert_clean("Policy No：HLT-1", "Policy No:HLT-1", ["nfkc"])

    def test_every_dash_becomes_a_hyphen(self):
        for dash in "‐‑‒–—―−":
            with self.subTest(dash=hex(ord(dash))):
                self.assertEqual(clean_line(f"Valid till: 31{dash}03{dash}2027")[0], "Valid till: 31-03-2027")

    def test_curly_quotes(self):
        self.assert_clean("‘Demo’ “Office”", "'Demo' \"Office\"", ["quote"])

    def test_rupee_words_before_an_amount_become_the_rupee_sign(self):
        for raw in ("Rs. 2,56,000/-", "Rs 2,56,000/-", "RS.2,56,000/-", "INR 2,56,000/-", "₨ 2,56,000/-", "rs.2,56,000/-"):
            with self.subTest(raw=raw):
                self.assertEqual(clean_line(f"Annual income: {raw}")[0], "Annual income: ₹2,56,000/-")

    def test_words_that_only_look_like_rupees_are_left_alone(self):
        for text in ("Mrs. 5 Demo Road", "Hrs 24", "Rs. only", "Rupees Two Lakh only", "PRS 12"):
            with self.subTest(text=text):
                self.assertEqual(clean_line(text), (text, []))

    def test_invisible_marks_and_extra_spaces(self):
        self.assert_clean("﻿Name:​  Meera   Rao ", "Name: Meera Rao", ["invisible", "spaces"])

    def test_hindi_joiners_are_kept(self):
        text = "क्‍ष"  # zero-width joiner changes how Hindi letters join
        self.assertEqual(clean_line(text)[0], text)

    def test_clean_line_changes_nothing_else(self):
        for text in ("Name: Meera Rao", "Aadhaar: 3399 8698 7973", "IFSC: DEM00206791", "Sum insured: 5,00,000"):
            with self.subTest(text=text):
                self.assertEqual(clean_line(text), (text, []))


class CleanPageTests(unittest.TestCase):
    def test_lines_record_rule_names_only(self):
        page = ocr_page([("Name: Meera Rao", 0.99), ("Annual income: Rs. 1,20,000", 0.98)])
        cleaned = clean_page(page)
        self.assertEqual(cleaned["text"], "Name: Meera Rao\nAnnual income: ₹1,20,000")
        self.assertNotIn("cleanup", cleaned["lines"][0])
        self.assertEqual(cleaned["lines"][1]["cleanup"], ["currency"])
        self.assertEqual(cleaned["lines"][1]["confidence"], 0.98)
        self.assertNotIn("Rs.", json.dumps(cleaned["lines"]))  # no copy of the old text kept

    def test_input_page_is_not_changed(self):
        page = ocr_page([("Valid till: 31–03–2027", 0.99)])
        before = json.dumps(page)
        clean_page(page)
        self.assertEqual(json.dumps(page), before)

    def test_line_that_cleans_to_nothing_is_dropped_so_text_and_lines_match(self):
        cleaned = clean_page(ocr_page([("Name: Meera Rao", 0.99), ("​", 0.5), ("Issue date: 2026-06-01", 0.99)]))
        self.assertEqual(len(cleaned["lines"]), len(cleaned["text"].splitlines()))
        self.assertEqual(extract_fields_from_pages([cleaned])["issue_date"]["source_ref"], "page:1:line:2")

    def test_page_without_lines_is_cleaned_by_text(self):
        cleaned = clean_page({"page": 1, "quality": 1.0, "text": "Annual income: INR 1,20,000"})
        self.assertEqual(cleaned["text"], "Annual income: ₹1,20,000")


class LabelMatchingTests(unittest.TestCase):
    def test_labels_with_dropped_spaces_dots_or_leaders(self):
        cases = {"PolicyNo": "policy_number", "Policy No.": "policy_number", "Validtill": "expiry_date",
                 "IFSC Code ........": "ifsc", "A/c No. ...........": "account_number", "Name..............": "name",
                 "ANNUAL INCOME": "annual_income", "Name": "name"}
        for label, key in cases.items():
            with self.subTest(label=label):
                self.assertEqual(field_for_label(label), key)

    def test_unknown_labels_still_do_not_match(self):
        for label in ("CIF No.", "Nominee", "Date", "Member ID", "Branch", ""):
            with self.subTest(label=label):
                self.assertIsNone(field_for_label(label))

    def test_passbook_lines_with_leaders_are_parsed(self):
        page = {"page": 1, "quality": 1.0, "text": "Name.............. : Meera Rao\n"
                                                  "A/c No. ........... :44719041229\nIFSC Code ......... : DEMO0206791"}
        fields = extract_fields_from_pages([page])
        self.assertEqual({k: v["value"] for k, v in fields.items()},
                         {"name": "Meera Rao", "account_number": "44719041229", "ifsc": "DEMO0206791"})


@patch("seniorlife_os.utils.privacy.find_env_file", return_value=None)
class GraphCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_fixtures()

    def run_upload(self, lines, doc_type):
        with patch("seniorlife_os.graphs.document_graph.read_document", return_value=[ocr_page(lines)]):
            with hash_key_env(FAKE_HASH_KEY):
                return build_document_graph().invoke(upload_state(UNIT / "sharp.png", "image/png", doc_type))

    def test_rupee_amount_written_with_rs_is_confirmed(self, _):
        result = self.run_upload([("Name: Meera Rao", 0.99), ("Annual income: Rs. 1,20,000", 0.99),
                                  ("Issue date: 2026–06–01", 0.99)], "income_certificate")
        self.assertEqual(result["status"], "confirmed")
        fields = result["vault_record"]["confirmed_fields"]
        self.assertEqual(fields["annual_income"]["value"], 120000)
        self.assertEqual(fields["issue_date"]["value"], "2026-06-01")
        self.assertEqual(result["document"]["pages"][0]["lines"][1]["cleanup"], ["currency"])

    def test_glued_policy_label_is_found_and_masked(self, _):
        result = self.run_upload([("Name: Meera Rao", 0.99), ("Insurer: Demo Health Insurance Ltd", 0.99),
                                  ("PolicyNo:HLT-2026/004512", 0.99), ("Sum insured: ₹5,00,000", 0.99),
                                  ("Validtill:2027-03-31", 0.99)], "health_insurance")
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["vault_record"]["confirmed_fields"]["policy_number"]["value"], "XXXXXXXXXXX4512")
        self.assertNotIn("HLT-2026/004512", json.dumps(result, default=str))

    def test_page_fixtures_are_not_cleaned(self, _):
        from seniorlife_os.demo.demo_cases import document_success_state

        state = document_success_state()
        before = state["document"]["pages"][0]["text"]
        with hash_key_env(FAKE_HASH_KEY):
            result = build_document_graph().invoke(state)
        self.assertEqual(result["document"]["pages"][0]["text"], before)


if __name__ == "__main__":
    unittest.main()
