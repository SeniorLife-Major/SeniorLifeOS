"""Tests for step 7c-5: finding fields in real document layouts (tools/layout.py + parser)."""

import json
import unittest
from unittest.mock import patch

from seniorlife_os.graphs.document_graph import build_document_graph
from seniorlife_os.tools.demo_tools import extract_fields_from_pages, label_kind, normalize_field
from seniorlife_os.tools.layout import STOP, label_above, no_colon_pair, pairs_in_line
from seniorlife_os.tools.ocr_postprocess import repair_value
from test_graph_ocr import ocr_page, upload_state
from test_ocr import UNIT, ensure_fixtures
from test_privacy import FAKE_HASH_KEY, hash_key_env

CONFIRM = {"action": "confirm", "reviewer_id": "senior-meera"}


def lookup(doc_type=None):
    return lambda label: label_kind(label, doc_type)


def part(text, x0, y0, width=300, confidence=0.99):
    return {"text": text, "confidence": confidence, "box": [x0, y0, x0 + width, y0 + 30]}


def line_of(*parts):
    return {"text": " ".join(p["text"] for p in parts), "confidence": min(p["confidence"] for p in parts),
            "box": [parts[0]["box"][0], parts[0]["box"][1], parts[-1]["box"][2], parts[-1]["box"][3]],
            "parts": list(parts)}


def card_page(rows, quality=0.99):
    lines = [line_of(*row) for row in rows]
    return {"page": 1, "quality": quality, "text": "\n".join(line["text"] for line in lines), "lines": lines,
            "engine": "fake", "sharpness": 500.0, "blurry": False}


# A two-column insurance card as OCR returns it: label row, then value row.
INSURANCE_CARD = [
    [part("Demo Health Insurance Co. Ltd.", 30, 20, 500)],
    [part("Insurer: Demo Health Insurance Ltd", 40, 80, 500)],
    [part("Member Name", 40, 150), part("Policy Number", 520, 150)],
    [part("Meera Rao", 40, 180), part("HLT/2026/004512", 520, 180)],
    [part("Member ID", 40, 270), part("Sum Insured", 520, 270)],
    [part("MID33221319", 40, 300), part("₹ 5,00,000", 520, 300)],
    [part("Valid From", 40, 390), part("Valid Upto", 520, 390)],
    [part("01-Aug-2028", 40, 420), part("28-Aug-2029", 520, 420, confidence=0.93)],
]


class PairsInLineTests(unittest.TestCase):
    def test_several_pairs_on_one_line_end_at_the_next_label(self):
        self.assertEqual(pairs_in_line("Patient Name : Smt. Anita Deshpande Age / Sex :69Y/F", lookup()),
                         [("name", "Smt. Anita Deshpande")])

    def test_date_label_counts_only_on_certificates(self):
        text = "Place: Demo Taluka Date: 23/12/2026 DEMO TALUKA OFFICE"
        self.assertEqual(pairs_in_line(text, lookup("income_certificate")),
                         [("issue_date", "23/12/2026 DEMO TALUKA OFFICE")])
        self.assertEqual(pairs_in_line(text, lookup("lab_report")), [])

    def test_time_colons_are_not_label_separators(self):
        self.assertEqual(pairs_in_line("Collected On: 20-Aug-2026 08:15 AM Reported On : Same day", lookup()), [])

    def test_colon_inside_a_value_is_kept(self):
        self.assertEqual(pairs_in_line("Policy No: HLT:2026/1", lookup()), [("policy_number", "HLT:2026/1")])

    def test_simple_lines_work_as_before(self):
        self.assertEqual(pairs_in_line("Name: Meera Rao", lookup()), [("name", "Meera Rao")])
        self.assertEqual(pairs_in_line("Name.............. : Meera Rao", lookup()), [("name", "Meera Rao")])
        self.assertEqual(pairs_in_line("Unknown label: something", lookup()), [])

    def test_bilingual_label_keeps_its_english_part(self):
        self.assertEqual(pairs_in_line("H/DOB:06/02/1940", lookup()), [("date_of_birth", "06/02/1940")])
        self.assertEqual(label_kind("Age / Sex"), STOP)  # a stop label is not split at its slash


class NoColonTests(unittest.TestCase):
    def test_label_then_value(self):
        self.assertEqual(no_colon_pair("Name Meera Rao", lookup()), ("name", "Meera Rao"))
        self.assertEqual(no_colon_pair("IFSC Code DEMO0206791", lookup()), ("ifsc", "DEMO0206791"))
        self.assertEqual(no_colon_pair("A/c No. 44719041229", lookup()), ("account_number", "44719041229"))

    def test_lines_that_only_start_like_a_label_are_ignored(self):
        for text in ("Bank of India", "Name", "Name: Meera Rao", "State-run hospital", "Nominee Registered"):
            with self.subTest(text=text):
                self.assertIsNone(no_colon_pair(text, lookup()))


class LabelAboveTests(unittest.TestCase):
    def test_columns_are_paired_by_position(self):
        label_row, value_row = line_of(*INSURANCE_CARD[2]), line_of(*INSURANCE_CARD[3])
        pairs = [(key, value["text"]) for key, value in label_above(label_row, value_row, lookup())]
        self.assertEqual(pairs, [("name", "Meera Rao"), ("policy_number", "HLT/2026/004512")])

    def test_stop_labels_are_skipped(self):
        pairs = label_above(line_of(*INSURANCE_CARD[4]), line_of(*INSURANCE_CARD[5]), lookup())
        self.assertEqual([(key, value["text"]) for key, value in pairs], [("sum_insured", "₹ 5,00,000")])

    def test_value_far_from_its_label_is_not_paired(self):
        pairs = label_above(line_of(part("Member Name", 40, 150)), line_of(part("Meera Rao", 600, 180)), lookup())
        self.assertEqual(pairs, [])

    def test_row_with_a_non_label_is_not_a_label_row(self):
        row = line_of(part("Member Name", 40, 150), part("Meera Rao", 520, 150))
        self.assertEqual(label_above(row, line_of(part("X", 40, 180)), lookup()), [])

    def test_text_only_pages_pair_a_whole_label_line_with_the_next(self):
        pairs = label_above({"text": "Member Name"}, {"text": "Meera Rao"}, lookup())
        self.assertEqual([(key, value["text"]) for key, value in pairs], [("name", "Meera Rao")])
        self.assertEqual(label_above({"text": "Member Name"}, {"text": "Policy Number"}, lookup()), [])


class ExtractionTests(unittest.TestCase):
    def test_insurance_card_fields_carry_layout_and_their_own_place(self):
        fields = extract_fields_from_pages([card_page(INSURANCE_CARD)], "health_insurance")
        self.assertEqual({k: v["value"] for k, v in fields.items()}, {
            "insurer": "Demo Health Insurance Ltd", "name": "Meera Rao", "policy_number": "HLT/2026/004512",
            "sum_insured": "₹ 5,00,000", "expiry_date": "28-Aug-2029"})
        self.assertNotIn("layout", fields["insurer"])  # found with a colon
        self.assertEqual(fields["expiry_date"]["layout"], "label_above")
        self.assertEqual(fields["expiry_date"]["source_ref"], "page:1:line:8")
        self.assertEqual(fields["expiry_date"]["confidence"], 0.93)  # the piece's own confidence
        self.assertEqual(fields["expiry_date"]["box"], [520, 420, 820, 450])

    def test_a_colon_field_wins_over_a_layout_guess(self):
        page = {"page": 1, "quality": 1.0, "text": "Name Someone Else\nName: Meera Rao"}
        fields = extract_fields_from_pages([page])
        self.assertEqual(fields["name"]["value"], "Meera Rao")
        self.assertNotIn("layout", fields["name"])


class RepairAndNameTests(unittest.TestCase):
    def test_extra_text_after_a_strict_value_is_cut(self):
        self.assertEqual(repair_value("issue_date", "23/12/2026 DEMO TALUKA OFFICE"),
                         ("23/12/2026", "2026-12-23", ["extra text after the value was ignored"]))
        self.assertEqual(repair_value("aadhaar_number", "3399 8698 7973 MALE")[1], "339986987973")

    def test_cut_and_lookalike_fix_together(self):
        self.assertEqual(repair_value("issue_date", "23/l2/2026 SAMPLE"),
                         ("23/12/2026", "2026-12-23",
                          ["look-alike characters corrected (l→1)", "extra text after the value was ignored"]))

    def test_free_text_fields_are_never_cut(self):
        self.assertIsNone(repair_value("name", "Meera Rao Age"))
        self.assertIsNone(repair_value("issue_date", "DEMO 23/12/2026"))  # only text *after* a value

    def test_titles_are_removed_from_names(self):
        for raw in ("Smt. Meera Rao", "Shri Meera Rao", "Dr Meera Rao", "MRS. Meera Rao", " Meera   Rao "):
            with self.subTest(raw=raw):
                self.assertEqual(normalize_field("name", raw), "Meera Rao")
        self.assertEqual(normalize_field("name", "Drishti Rao"), "Drishti Rao")  # 'Dr' inside a word stays


@patch("seniorlife_os.utils.privacy.find_env_file", return_value=None)
class GraphLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_fixtures()

    def run_upload(self, page, doc_type, **extra):
        with patch("seniorlife_os.graphs.document_graph.read_document", return_value=[page]), hash_key_env(FAKE_HASH_KEY):
            return build_document_graph().invoke(upload_state(UNIT / "sharp.png", "image/png", doc_type, **extra))

    def test_layout_fields_are_sent_to_review(self, _):
        result = self.run_upload(card_page(INSURANCE_CARD), "health_insurance")
        self.assertEqual(result["status"], "needs_review")
        for key in ("name", "policy_number", "sum_insured", "expiry_date"):
            self.assertIn(f"{key}: found by layout (label above), please check", result["review_flags"])

    def test_owner_confirms_the_card_and_pieces_are_dropped_with_the_number_masked(self, _):
        result = self.run_upload(card_page(INSURANCE_CARD), "health_insurance", review_decision=CONFIRM)
        self.assertEqual(result["status"], "confirmed")
        fields = result["vault_record"]["confirmed_fields"]
        self.assertEqual(fields["sum_insured"]["value"], 500000)
        self.assertEqual(fields["expiry_date"]["value"], "2029-08-28")
        self.assertEqual(fields["policy_number"]["value"], "XXXXXXXXXXX4512")
        self.assertFalse(any("parts" in line for line in result["document"]["pages"][0]["lines"]))
        self.assertNotIn("HLT/2026/004512", json.dumps(result, default=str))

    def test_certificate_date_beside_a_stamp_is_cut_and_reviewed(self, _):
        page = ocr_page([("Name: Smt. Meera Rao", 0.99), ("Annual income: Rs. 2,56,000/-", 0.99),
                         ("Place: Demo Taluka Date: 23/12/2025 DEMO TALUKA OFFICE SAMPLE", 0.99)])
        result = self.run_upload(page, "income_certificate")
        self.assertEqual(result["normalized_fields"]["issue_date"], "2025-12-23")
        self.assertEqual(result["normalized_fields"]["name"], "Meera Rao")
        self.assertIn("issue_date: extra text after the value was ignored, please check", result["review_flags"])
        self.assertNotIn("name differs from the provided owner name", result["review_flags"])


if __name__ == "__main__":
    unittest.main()
