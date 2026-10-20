"""Tests for step 7c-3: reading Indian date, rupee and state formats (tools/demo_tools.py)."""

import unittest
from unittest.mock import patch

from seniorlife_os.demo.demo_cases import document_success_state
from seniorlife_os.graphs.document_graph import build_document_graph
from seniorlife_os.tools.demo_tools import INDIAN_STATES, normalize_field, parse_amount, parse_date, parse_state
from test_graph_ocr import ocr_page, upload_state
from test_ocr import UNIT, ensure_fixtures
from test_privacy import FAKE_HASH_KEY, hash_key_env


class DateTests(unittest.TestCase):
    def test_accepted_forms_all_mean_the_same_day(self):
        for raw in ("2026-12-23", "23/12/2026", "23-12-2026", "23.12.2026", "23-Dec-2026", "23 Dec 2026",
                    "23 December 2026", "23-DEC-2026", "23 Dec, 2026", " 23/12/2026 "):
            with self.subTest(raw=raw):
                self.assertEqual(parse_date(raw), "2026-12-23")

    def test_single_digit_day_and_month_are_day_first(self):
        self.assertEqual(parse_date("3/2/2026"), "2026-02-03")
        self.assertEqual(parse_date("28-Sept-2029"), "2029-09-28")

    def test_unclear_or_impossible_dates_are_refused_not_guessed(self):
        for raw in ("2026/06/01",   # year first with slashes: the demo's deliberately bad date
                    "23/12/26",     # two-digit year
                    "31/02/2026", "2026-02-30", "32-01-2026", "23/13/2026",
                    "23/12-2026",   # mixed separators
                    "28 Augus 2029", "28-Foo-2029", "2026-6-1", "", "tomorrow"):
            with self.subTest(raw=raw):
                self.assertIsNone(parse_date(raw))


class AmountTests(unittest.TestCase):
    def test_rupee_forms(self):
        cases = {"₹2,56,000/-": 256000, "Rs. 2,56,000/-": 256000, "RS 2,56,000": 256000, "INR 2,56,000/=": 256000,
                 "₹ 1,00,00,000": 10_000_000, "256,000": 256000, "256000": 256000, "256000.00": 256000,
                 "₹1,20,000": 120000, "950": 950}
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(parse_amount(raw), expected)

    def test_misplaced_separators_and_paise_are_refused(self):
        for raw in ("2.56.000", "2,5,6000", "25,60,00", "1,20,000.50", "₹", "", "two lakh", "12a000"):
            with self.subTest(raw=raw):
                self.assertIsNone(parse_amount(raw))

    def test_a_misread_rupee_sign_still_looks_valid(self):
        # "₹2,66,000" read as "22,66,000" is a valid-looking number; format checks can't catch it.
        # It is caught by line confidence or the blur cap, and owner review (see Decision #19).
        self.assertEqual(parse_amount("22,66,000"), 2266000)


class StateTests(unittest.TestCase):
    def test_names_and_codes(self):
        cases = {"Kerala": "KL", "KERALA": "KL", "MH": "MH", "mh": "MH", "Maharashtra.": "MH",
                 "State of Tamil Nadu": "TN", "the State of Gujarat": "GJ", "Jammu & Kashmir": "JK",
                 "NCT of Delhi": "DL", "Orissa": "OD", "Pondicherry": "PY", "TS": "TG", "CG": "CT"}
        for raw, code in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(parse_state(raw), code)

    def test_unknown_states_are_refused(self):
        for raw in ("Atlantis", "XX", "", "Pune"):
            with self.subTest(raw=raw):
                self.assertIsNone(parse_state(raw))

    def test_every_state_and_union_territory_is_listed_once_by_code(self):
        self.assertEqual(len(set(INDIAN_STATES.values())), 36)  # 28 states + 8 union territories

    def test_normalize_field_uses_the_new_readers(self):
        self.assertEqual(normalize_field("residence_state", "Kerala"), "KL")
        self.assertIsNone(normalize_field("residence_state", "Atlantis"))  # was "ATLANTIS" before 7c-3
        self.assertEqual(normalize_field("expiry_date", "28-Aug-2029"), "2029-08-28")
        self.assertEqual(normalize_field("sum_insured", "₹ 5,00,000"), 500000)


@patch("seniorlife_os.utils.privacy.find_env_file", return_value=None)
class GraphFormatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_fixtures()

    def run_upload(self, lines, doc_type, **extra):
        page = ocr_page([(text, 0.99) for text in lines])
        with patch("seniorlife_os.graphs.document_graph.read_document", return_value=[page]), hash_key_env(FAKE_HASH_KEY):
            return build_document_graph().invoke(upload_state(UNIT / "sharp.png", "image/png", doc_type, **extra))

    def test_certificate_with_indian_formats_is_confirmed(self, _):
        result = self.run_upload(["Name: Meera Rao", "Annual income: Rs. 2,56,000/-", "Issue date: 23/12/2025"],
                                 "income_certificate")
        self.assertEqual(result["status"], "confirmed")
        fields = result["vault_record"]["confirmed_fields"]
        self.assertEqual(fields["annual_income"]["value"], 256000)
        self.assertEqual(fields["issue_date"]["value"], "2025-12-23")
        self.assertEqual(result["vault_record"]["issue_date"], "2025-12-23")

    def test_residence_state_name_becomes_its_code(self, _):
        result = self.run_upload(["Name: Meera Rao", "State: Maharashtra", "Issue date: 01.02.2026"], "residence_proof")
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["vault_record"]["confirmed_fields"]["residence_state"]["value"], "MH")

    def test_insurance_expiry_with_month_name(self, _):
        result = self.run_upload(["Name: Meera Rao", "Insurer: Demo Health Insurance Ltd", "Policy No: HLT/2026/004512",
                                  "Sum insured: ₹ 5,00,000", "Valid till: 28-Aug-2029"], "health_insurance")
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["vault_record"]["expiry_date"], "2029-08-28")

    def test_misread_amount_goes_to_review(self, _):
        result = self.run_upload(["Name: Meera Rao", "Annual income: 2.56.000", "Issue date: 23/12/2025"],
                                 "income_certificate")
        self.assertEqual(result["status"], "needs_review")
        self.assertIn("annual_income: missing or invalid", result["review_flags"])

    def test_owner_can_type_a_day_first_date_when_correcting(self, _):
        state = document_success_state()
        state["review_decision"]["edits"] = {"issue_date": "01/06/2026"}
        with hash_key_env(FAKE_HASH_KEY):
            result = build_document_graph().invoke(state)
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["vault_record"]["confirmed_fields"]["issue_date"]["value"], "2026-06-01")

    def test_demo_year_first_date_still_needs_the_owner(self, _):
        state = document_success_state()
        state.pop("review_decision")
        with hash_key_env(FAKE_HASH_KEY):
            result = build_document_graph().invoke(state)
        self.assertEqual(result["status"], "needs_review")
        self.assertIn("issue_date: missing or invalid", result["review_flags"])


if __name__ == "__main__":
    unittest.main()
