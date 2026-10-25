"""Tests for steps 6b/6c: lab reports, prescriptions and medical bills (tools/medical.py + graph)."""

import json
import unittest
from unittest.mock import patch

from backend.demo.demo_cases import lab_report_state, medical_bill_state, prescription_state
from backend.graphs.document_graph import build_document_graph
from backend.tools.medical import (
    BILL_COLUMNS, _header_columns, extract_lists, item_rows, lab_flag, lab_rows, medicine_rows, normalize_rows,
    parse_money, tidy_range,
)
from backend.tools.ocr import group_lines
from test_graph_ocr import upload_state
from test_llm_extraction import FakeStructuredModel
from test_ocr import UNIT, ensure_fixtures
from test_privacy import FAKE_HASH_KEY, hash_key_env

CONFIRM = {"action": "confirm", "reviewer_id": "senior-meera"}
TABLE = [("Haemoglobin", "15.2", "g/dL", "12.0 - 15.5"), ("TSH", "5.5", "uIU/mL", "0.4 - 4.0"),
         ("Total Cholesterol", "252.4", "mg/dL", "< 200"), ("Fasting Blood Sugar", "102.4", "mg/dL", "70 - 100")]
COLUMNS = (60, 520, 720, 900)


def tilted_lab_page(slope):
    """OCR boxes for a results table photographed with a tilt: y drifts by slope * x.

    The boxes go through the real line grouping of tools/ocr.py, exactly as PaddleOCR
    output would, so a tilt makes some lines mix pieces from neighbouring rows.
    """
    items = []

    def add(text, x, row_y, width=150):
        y = row_y + slope * x
        items.append(([(x, y), (x + width, y), (x + width, y + 30), (x, y + 30)], text, 0.98))

    add("Patient Name : Meera Rao", 60, 40, 400)
    for text, x in zip(("Test", "Result", "Unit", "Reference Range"), COLUMNS):
        add(text, x, 120)
    for row, cells in enumerate(TABLE):
        for text, x in zip(cells, COLUMNS):
            add(text, x, 170 + 50 * row, 300 if x == 60 else 150)
    lines = group_lines(items)
    return {"page": 1, "quality": 0.98, "text": "\n".join(line["text"] for line in lines), "lines": lines,
            "engine": "fake", "sharpness": 500.0, "blurry": False}


def by_name(rows):
    return {row["name"]: (row.get("value"), row.get("unit"), row.get("ref_range")) for row in rows}


EXPECTED = {name: (value, unit, ref_range) for name, value, unit, ref_range in TABLE}


class LabTableTests(unittest.TestCase):
    def test_straight_photo(self):
        self.assertEqual(by_name(lab_rows(tilted_lab_page(0.0))), EXPECTED)

    def test_tilted_photo_keeps_each_value_on_its_own_test(self):
        page = tilted_lab_page(0.06)  # about 3.4 degrees: the right columns drift down by a whole row
        plain = {line.split()[0] for line in page["text"].splitlines()}
        self.assertNotEqual(by_name(lab_rows({**page, "lines": None})), EXPECTED)  # the old failure is real
        self.assertEqual(by_name(lab_rows(page)), EXPECTED)
        self.assertTrue(plain)  # (the page really was built)

    def test_rows_point_to_the_line_of_their_test_name(self):
        rows = lab_rows(tilted_lab_page(0.06))
        page = tilted_lab_page(0.06)
        for row in rows:
            line_number = int(row["source_ref"].split(":")[3])
            self.assertIn(row["name"], page["text"].splitlines()[line_number - 1])

    def test_text_only_table(self):
        page = {"page": 1, "quality": 1.0, "text": "Test Result Unit Reference Range\n"
                                                   "Haemoglobin 11.2 g/dL 12.0 - 15.5\nHIV Negative\n"
                                                   "*** End of Report ***\nNot a row 5"}
        rows = lab_rows(page)
        self.assertEqual([(r["name"], r["value"], r["unit"], r["ref_range"]) for r in rows],
                         [("Haemoglobin", "11.2", "g/dL", "12.0 - 15.5"), ("HIV", "Negative", None, None)])

    def test_no_results_header_means_no_rows(self):
        self.assertEqual(lab_rows({"page": 1, "text": "Haemoglobin 11.2 g/dL"}), [])


class ParsingTests(unittest.TestCase):
    def test_flags_come_only_from_the_printed_range(self):
        cases = [(11.2, "12.0 - 15.5", "low"), (16, "12.0 - 15.5", "high"), (13, "12.0 - 15.5", "normal"),
                 (252.4, "< 200", "high"), (150, "< 200", "normal"), (30, "> 40", "low"), (55, "> 40", "normal"),
                 (5, "see note", None), (5, None, None), (None, "1 - 2", None)]
        for value, ref_range, flag in cases:
            with self.subTest(value=value, ref_range=ref_range):
                self.assertEqual(lab_flag(value, ref_range), flag)

    def test_money_with_paise(self):
        cases = {"₹1,250.50": 1250.5, "Rs. 1,250/-": 1250.0, "1250.5": 1250.5, "12,34,567.00": 1234567.0,
                 "1.250.50": None, "12,3,4": None, "": None}
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(parse_money(raw), expected)

    def test_row_problems_are_listed_for_the_reviewer(self):
        rows = normalize_rows("tests", [{"name": "Haemoglobin", "value": "1l.2"}, {"name": "", "value": "5"},
                                        {"name": "HIV", "value": "Negative"}])
        self.assertEqual(rows[0]["problems"], ["value is not a number"])
        self.assertEqual(rows[1]["problems"], ["no test name"])
        self.assertNotIn("problems", rows[2])
        self.assertIsNone(normalize_rows("tests", []))

    def test_range_spacing_is_made_consistent_without_touching_numbers(self):
        for raw, tidy in (("0.6-1.2", "0.6 - 1.2"), ("12.0 -15.5", "12.0 - 15.5"), ("<200", "< 200"),
                          (">= 40", ">= 40"), ("see note", "see note")):
            with self.subTest(raw=raw):
                self.assertEqual(tidy_range(raw), tidy)
        self.assertEqual(normalize_rows("tests", [{"name": "TSH", "value": "5.5", "ref_range": "0.4-4.0"}])[0]
                         ["ref_range"], "0.4 - 4.0")

    def test_medicine_lines(self):
        page = {"page": 1, "text": "Rx\n1. Tab. Metformin 500 mg 1-0-1 x 30 days\nCap Omeprazole 20mg BD\n"
                                   "Syp. Cough Relief 10 ml at night for 5 days\nAdvice: walk daily"}
        rows = medicine_rows(page)
        self.assertEqual([(r["name"], r["form"], r["strength"], r["frequency"], r["duration"]) for r in rows], [
            ("Metformin", "Tab", "500 mg", "1-0-1", "x 30 days"),
            ("Omeprazole", "Cap", "20mg", "BD", None),
            ("Cough Relief", "Syp", "10 ml", "at night", "for 5 days")])

    def test_bill_items_stop_at_the_total(self):
        page = {"page": 1, "text": "S.No Description Qty Rate Amount\n1 Metformin 500mg (10 tabs) 2 60.00 120.00\n"
                                   "2 Consultation 500.00\nSub Total 620.00\nGrand Total: ₹620.00\n3 After 1.00"}
        rows = item_rows(page)
        self.assertEqual([(r["description"], r["amount"]) for r in rows],
                         [("Metformin 500mg (10 tabs)", "120.00"), ("Consultation", "500.00")])

    def test_only_medical_types_have_lists(self):
        page = {"page": 1, "quality": 1.0, "text": "Test Result Unit\nHaemoglobin 11.2 g/dL"}
        self.assertEqual(extract_lists([page], "income_certificate"), {})
        self.assertIn("tests", extract_lists([page], "lab_report"))


class MergedHeaderTests(unittest.TestCase):
    def test_joined_header_names_get_their_own_left_edges(self):
        piece = {"text": "S.No Description", "box": [100, 0, 420, 30]}
        found = _header_columns(piece, BILL_COLUMNS)
        self.assertEqual([column for column, _ in found], ["serial", "description"])
        self.assertEqual(found[0][1], 100)
        self.assertAlmostEqual(found[1][1], 100 + 320 * 5 / 16)

    def test_a_piece_with_other_words_is_not_a_header(self):
        self.assertEqual(_header_columns({"text": "Amount in words", "box": [0, 0, 100, 30]}, BILL_COLUMNS), [])


HARD = UNIT.parent / "ocr_hard"


class RealOcrOutputTests(unittest.TestCase):
    """What PaddleOCR really returned for the hard fake lab report and bill (all 6 versions)."""

    @classmethod
    def setUpClass(cls):
        if not (HARD / "results_paddle.json").exists():
            raise unittest.SkipTest("hard dataset results missing (run tests/make_hard_dataset.py --score)")
        cls.saved = json.loads((HARD / "results_paddle.json").read_text(encoding="utf-8"))["pages"]
        cls.manifest = {e["path"]: e for e in json.loads((HARD / "manifest.json").read_text(encoding="utf-8"))}

    def pages(self, doc_type):
        for saved in self.saved:
            if saved["type"] == doc_type:
                yield saved, {"page": 1, "quality": saved["quality"], "text": saved["text"], "lines": saved["lines"]}

    def test_no_lab_value_lands_on_another_test(self):
        for saved, page in self.pages("lab_report"):
            with self.subTest(version=saved["condition"]):
                truth = {t["name"]: t["value"] for t in self.manifest[saved["path"]]["fields"]["tests"]["value"]}
                rows = normalize_rows("tests", extract_lists([page], "lab_report")["tests"]["value"])
                for row in rows:
                    self.assertIn(row["name"], truth)
                    if isinstance(row["value"], float):  # a value OCR lost is reported, never borrowed
                        self.assertEqual(row["value"], truth[row["name"]], row["name"])

    def test_bill_items_on_every_version(self):
        for saved, page in self.pages("medical_bill"):
            with self.subTest(version=saved["condition"]):
                truth = [(i["description"], i["amount"]) for i in self.manifest[saved["path"]]["fields"]["items"]["value"]]
                rows = normalize_rows("items", extract_lists([page], "medical_bill")["items"]["value"])
                self.assertEqual([r["amount"] for r in rows], [amount for _, amount in truth])
                self.assertEqual(len(rows), len(truth))


@patch("backend.utils.privacy.find_env_file", return_value=None)
class GraphMedicalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_fixtures()

    def run_state(self, state, fake=None):
        with patch("backend.agents.llm_agents.structured_model", fake or FakeStructuredModel({})), \
                hash_key_env(FAKE_HASH_KEY):
            return build_document_graph().invoke(state)

    def test_medical_documents_are_never_auto_accepted(self, _):
        for make, key, count in ((lab_report_state, "tests", 3), (prescription_state, "medicines", 2),
                                 (medical_bill_state, "items", 2)):
            with self.subTest(document=make.__name__):
                result = self.run_state(make())
                self.assertEqual(result["status"], "needs_review")
                self.assertIn(f"{key}: please check every row ({count})", result["review_flags"])

    def test_owner_confirms_a_lab_report(self, _):
        result = self.run_state({**lab_report_state(), "review_decision": CONFIRM})
        self.assertEqual(result["status"], "confirmed")
        tests = result["vault_record"]["confirmed_fields"]["tests"]["value"]
        self.assertEqual([(t["name"], t["value"], t["flag"]) for t in tests],
                         [("Haemoglobin", 11.2, "low"), ("Fasting Blood Sugar", 98.0, "normal"),
                          ("Total Cholesterol", 232.0, "high")])

    def test_owner_can_correct_the_whole_list(self, _):
        edits = {"medicines": [{"name": "Metformin", "strength": "500 mg", "frequency": "1-0-1"}]}
        result = self.run_state({**prescription_state(), "review_decision": {**CONFIRM, "edits": edits}})
        self.assertEqual(result["status"], "confirmed")
        medicines = result["vault_record"]["confirmed_fields"]["medicines"]
        self.assertEqual([m["name"] for m in medicines["value"]], ["Metformin"])
        self.assertTrue(medicines["corrected_by_user"])

    def test_emptied_list_is_refused(self, _):
        result = self.run_state({**lab_report_state(), "review_decision": {**CONFIRM, "edits": {"tests": []}}})
        self.assertEqual(result["status"], "needs_review")
        self.assertIn("Correct these required fields: tests", result["errors"])

    def test_bill_items_must_add_up_to_the_total(self, _):
        state = medical_bill_state()
        page = state["document"]["pages"][0]
        page["text"] = page["text"].replace("Grand Total: ₹165.50", "Grand Total: ₹265.50")
        result = self.run_state(state)
        self.assertIn("items add up to 165.50 but the total says 265.50", result["review_flags"])

    def test_future_report_date_is_refused_at_review(self, _):
        state = lab_report_state()
        page = state["document"]["pages"][0]
        page["text"] = page["text"].replace("Reported On: 2026-09-10", "Reported On: 2026-12-10")
        result = self.run_state({**state, "review_decision": CONFIRM})
        self.assertEqual(result["errors"], ["Report date cannot be in the future"])
        self.assertIn("human_field_review:future_date", result["events"])

    def test_no_cloud_model_for_medical_documents_without_consent(self, _):
        state = lab_report_state()
        state["document"]["pages"][0]["text"] = "Patient Name: Meera Rao\nsome unreadable lab page"
        fake = FakeStructuredModel({"tests": []})
        result = self.run_state({**state, "use_llm": True}, fake)
        self.assertEqual(fake.prompts, [])
        self.assertNotIn("extract_content:missing_fields", result["events"])

    def test_medical_page_is_typed_on_this_machine_without_consent(self, _):
        for make, expected in ((lab_report_state, "lab_report"), (prescription_state, "prescription"),
                               (medical_bill_state, "medical_bill")):
            with self.subTest(document=expected):
                state = make()
                state["document"]["filename"] = "scan_0001.jpg"  # no hint in the name
                fake = FakeStructuredModel({"document_type": "identity", "reason": "x"})
                result = self.run_state({**state, "use_llm": True}, fake)
                self.assertEqual(fake.prompts, [])
                self.assertIn(f"classify_document:{expected}", result["events"])

    def test_with_consent_the_cloud_model_may_type_a_medical_page(self, _):
        state = lab_report_state()
        fake = FakeStructuredModel({"document_type": "lab_report", "reason": "results table"})
        result = self.run_state({**state, "use_llm": True, "medical_cloud_consent": True}, fake)
        self.assertEqual(len(fake.prompts), 1)
        self.assertIn("classify_document:lab_report", result["events"])

    def test_with_consent_the_model_may_fill_rows_that_are_in_the_text(self, _):
        state = lab_report_state()
        state["document"]["pages"][0]["text"] = ("DEMO LAB\nPatient Name: Meera Rao\nReported On: 2026-09-10\n"
                                                 "Haemoglobin was 11.2 g/dL (12.0 - 15.5)")
        fake = FakeStructuredModel({"tests": [
            {"name": "Haemoglobin", "value": "11.2", "unit": "g/dL", "ref_range": "12.0 - 15.5"},
            {"name": "Vitamin D", "value": "8.1", "unit": "ng/mL"},  # not in the text: dropped
        ]})
        state["claimed_type"] = "lab_report"  # this test is about the values, not the type
        result = self.run_state({**state, "use_llm": True, "medical_cloud_consent": True}, fake)
        self.assertIn("llm_extract:found_1_of_1", result["events"])
        self.assertEqual(set(fake.schemas[0].model_fields), {"tests"})
        tests = result["normalized_fields"]["tests"]
        self.assertEqual([(t["name"], t["value"], t["flag"]) for t in tests], [("Haemoglobin", 11.2, "low")])
        self.assertEqual(tests[0]["source_ref"], "page:1:line:4")
        self.assertIn("tests: found by AI reading the text, please check", result["review_flags"])
        self.assertNotIn("Meera Rao", fake.prompts[0])

    def test_tilted_lab_photo_through_the_graph(self, _):
        page = tilted_lab_page(0.06)
        page["text"] = "DEMO DIAGNOSTICS LAB\nReported On: 2026-09-10\n" + page["text"]
        page["lines"] = [{"text": "DEMO DIAGNOSTICS LAB", "confidence": 0.99, "box": [0, 0, 400, 30]},
                         {"text": "Reported On: 2026-09-10", "confidence": 0.99, "box": [0, 0, 400, 30]},
                         *page["lines"]]
        with patch("backend.graphs.document_graph.read_document", return_value=[page]):
            result = self.run_state(upload_state(UNIT / "sharp.png", "image/png", "lab_report",
                                                 review_decision=CONFIRM))
        self.assertEqual(result["status"], "confirmed")
        tests = result["vault_record"]["confirmed_fields"]["tests"]["value"]
        self.assertEqual({t["name"]: t["value"] for t in tests},
                         {"Haemoglobin": 15.2, "TSH": 5.5, "Total Cholesterol": 252.4, "Fasting Blood Sugar": 102.4})
        self.assertFalse(any("parts" in line for line in result["document"]["pages"][0]["lines"]))
        self.assertNotIn("Meera", json.dumps(result["vault_record"]["confirmed_fields"]["tests"]))


if __name__ == "__main__":
    unittest.main()
