"""Tests for step 7c-1: real files going through the document graph.

validate_upload -> read_file (local OCR) -> classify -> extract -> ... -> publish.
Fast tests use real PDFs that store text (no OCR) or replace read_document with a
stand-in. The real-PaddleOCR tests run only with SENIORLIFE_RUN_OCR_TESTS=1.
"""

import json
import unittest
from statistics import fmean
from unittest.mock import patch

from backend.demo.demo_cases import document_success_state
from backend.graphs.document_graph import OCR_MAX_ATTEMPTS, build_document_graph
from backend.tools.demo_tools import extract_fields_from_pages
from backend.tools.ocr import OcrEngineError
from test_ocr import BENCHMARK, RUN_SLOW, UNIT, ensure_fixtures
from test_privacy import FAKE_HASH_KEY, hash_key_env

AADHAAR = "3399 8698 7973"  # fictional, checksum-valid (from the benchmark set)


def upload_state(path, mime_type, claimed_type=None, **extra):
    state = {
        "actor_id": "senior-meera",
        "document_id": "doc-upload-001",
        "document": {
            "owner_id": "senior-meera",
            "owner_name": "Meera Rao",
            "filename": path.name,
            "mime_type": mime_type,
            "file_path": str(path),
            "as_of": "2026-09-22",
        },
        **extra,
    }
    if claimed_type:
        state["claimed_type"] = claimed_type
    return state


def ocr_page(lines, blurry=False, quality=None):
    """A page shaped like tools/ocr.py output. lines: [(text, confidence), ...]"""
    entries = [{"text": text, "confidence": conf, "box": [10, 40 * i, 600, 40 * i + 30]}
               for i, (text, conf) in enumerate(lines)]
    if quality is None:
        quality = round(fmean(conf for _, conf in lines), 4)
    return {"page": 1, "quality": quality, "text": "\n".join(text for text, _ in lines), "lines": entries,
            "engine": "fake", "sharpness": 20.0 if blurry else 500.0, "blurry": blurry}


INCOME_LINES = [("Name: Meera Rao", 0.70), ("Annual income: 1,20,000", 0.99), ("Issue date: 2026-06-01", 0.99)]
IDENTITY_LINES = [("Name: Meera Rao", 0.99), ("Date of birth: 1950-09-17", 0.99), (f"Aadhaar: {AADHAAR}", 0.99)]


@patch("backend.utils.privacy.find_env_file", return_value=None)
class UploadedFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_fixtures()
        cls.too_big = UNIT / "too_big.png"
        if not cls.too_big.exists():
            cls.too_big.write_bytes(b"\0" * 5_000_001)

    def run_graph(self, state):
        with hash_key_env(FAKE_HASH_KEY):
            return build_document_graph().invoke(state)

    def test_digital_pdf_is_confirmed_end_to_end(self, _):
        result = self.run_graph(upload_state(UNIT / "digital.pdf", "application/pdf", "health_insurance"))
        self.assertEqual(result["status"], "confirmed")
        self.assertIn("read_file:pdf_text", result["events"])
        self.assertIn("confidence_gate:auto_accept", result["events"])
        fields = result["vault_record"]["confirmed_fields"]
        self.assertEqual(fields["sum_insured"]["value"], 500000)
        self.assertEqual(fields["policy_number"]["source_ref"], "document:doc-upload-001:page:1:line:3")
        self.assertNotIn("HLT-2026/004512", json.dumps(result, default=str))

    def test_page_fixtures_still_skip_ocr(self, _):
        result = self.run_graph(document_success_state())
        self.assertIn("read_file:fixture", result["events"])
        self.assertEqual(result["status"], "confirmed")

    def test_size_is_taken_from_the_file_not_the_claim(self, _):
        state = upload_state(self.too_big, "image/png", "identity")
        state["document"]["size_bytes"] = 1000  # the client claims a small file
        result = self.run_graph(state)
        self.assertEqual(result["status"], "rejected")
        self.assertIn("File must be between 1 byte and 5 MB", result["errors"])

    def test_missing_file_is_rejected_once(self, _):
        result = self.run_graph(upload_state(UNIT / "not_there.png", "image/png"))
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["errors"], ["Uploaded file not found"])

    def test_file_and_page_fixtures_together_are_rejected(self, _):
        state = upload_state(UNIT / "digital.pdf", "application/pdf")
        state["document"]["pages"] = [{"page": 1, "quality": 1.0, "text": "Name: Meera Rao"}]
        result = self.run_graph(state)
        self.assertIn("Give either file_path or page fixtures, not both", result["errors"])

    def test_broken_file_asks_for_reupload_without_retrying(self, _):
        result = self.run_graph(upload_state(UNIT / "broken.pdf", "application/pdf", "identity"))
        self.assertEqual(result["status"], "needs_reupload")
        self.assertIn("check_file:reupload", result["events"])  # found before OCR since the file checks
        self.assertIn("The PDF file is damaged", result["warnings"])
        self.assertFalse(any("retry" in event for event in result["events"]))

    def test_engine_failure_is_retried_then_succeeds(self, _):
        page = ocr_page([(text, 0.99) for text, _ in INCOME_LINES])
        with patch("backend.graphs.document_graph.read_document",
                   side_effect=[OcrEngineError("crash"), OcrEngineError("crash"), [page]]) as reader:
            result = self.run_graph(upload_state(UNIT / "sharp.png", "image/png", "income_certificate"))
        self.assertEqual(reader.call_count, 3)
        self.assertIn("read_file:retry_1", result["events"])
        self.assertIn("read_file:retry_2", result["events"])
        self.assertIn("read_file:fake", result["events"])
        self.assertEqual(result["status"], "confirmed")

    def test_engine_that_keeps_failing_stops_the_run(self, _):
        with patch("backend.graphs.document_graph.read_document", side_effect=OcrEngineError("crash")) as reader:
            result = self.run_graph(upload_state(UNIT / "sharp.png", "image/png", "income_certificate"))
        self.assertEqual(reader.call_count, OCR_MAX_ATTEMPTS)
        self.assertEqual(result["status"], "failed_tool")
        self.assertIn("read_file:engine_failed", result["events"])
        self.assertNotIn("vault_record", result)

    def test_file_with_no_text_asks_for_reupload(self, _):
        blank = {"page": 1, "quality": 0.0, "text": "", "lines": [], "engine": "fake", "sharpness": 0.0, "blurry": False}
        with patch("backend.graphs.document_graph.read_document", return_value=[blank]):
            result = self.run_graph(upload_state(UNIT / "sharp.png", "image/png", "identity"))
        self.assertEqual(result["status"], "needs_reupload")
        self.assertIn("read_file:no_text", result["events"])

    def test_each_field_uses_its_own_line_confidence(self, _):
        with patch("backend.graphs.document_graph.read_document", return_value=[ocr_page(INCOME_LINES)]):
            result = self.run_graph(upload_state(UNIT / "sharp.png", "image/png", "income_certificate"))
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["review_flags"], ["name: low confidence (0.70)"])
        self.assertEqual(result["extracted_fields"]["name"]["box"], [10, 0, 600, 30])

    def test_blurry_page_sends_every_field_to_review(self, _):
        page = ocr_page([(text, 0.99) for text, _ in INCOME_LINES], blurry=True, quality=0.85)
        with patch("backend.graphs.document_graph.read_document", return_value=[page]):
            result = self.run_graph(upload_state(UNIT / "sharp.png", "image/png", "income_certificate"))
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(sorted(result["review_flags"]), [
            "annual_income: low confidence (0.85)", "issue_date: low confidence (0.85)", "name: low confidence (0.85)",
        ])

    def test_full_id_number_is_masked_in_every_ocr_line(self, _):
        with patch("backend.graphs.document_graph.read_document", return_value=[ocr_page(IDENTITY_LINES)]):
            result = self.run_graph(upload_state(UNIT / "sharp.png", "image/png", "identity"))
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["document"]["pages"][0]["lines"][2]["text"], "Aadhaar: XXXX XXXX 7973")
        dumped = json.dumps(result, default=str)
        self.assertNotIn(AADHAAR, dumped)
        self.assertNotIn(AADHAAR.replace(" ", ""), dumped)


class LineConfidenceParserTests(unittest.TestCase):
    def test_lines_that_do_not_match_the_text_fall_back_to_page_quality(self):
        page = ocr_page(INCOME_LINES)
        page["lines"] = page["lines"][:2]  # out of step with the text: don't trust it
        fields = extract_fields_from_pages([page])
        self.assertEqual(fields["name"]["confidence"], page["quality"])
        self.assertNotIn("box", fields["name"])

    def test_fixture_pages_keep_page_quality(self):
        fields = extract_fields_from_pages([{"page": 1, "quality": 0.92, "text": "Name: Meera Rao"}])
        self.assertEqual(fields["name"]["confidence"], 0.92)


@unittest.skipUnless(RUN_SLOW, "set SENIORLIFE_RUN_OCR_TESTS=1 to run real PaddleOCR (a few minutes)")
@patch("backend.utils.privacy.find_env_file", return_value=None)
class RealOcrGraphTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        manifest = BENCHMARK / "manifest.json"
        if not manifest.exists():
            raise unittest.SkipTest(f"benchmark dataset missing: {manifest}")
        cls.manifest = {entry["path"]: entry for entry in json.loads(manifest.read_text(encoding="utf-8"))}

    def run_page(self, name, doc_type):
        state = upload_state(BENCHMARK / "pages" / name, "image/png", doc_type)
        truth = dict(self.manifest[f"pages/{name}"]["fields"])
        state["document"]["owner_name"] = truth["Name"]
        with hash_key_env(FAKE_HASH_KEY):
            return build_document_graph().invoke(state), truth

    def test_clean_identity_photo_is_confirmed_and_masked(self, _):
        result, truth = self.run_page("identity__clean.png", "identity")
        self.assertIn("read_file:paddleocr", result["events"])
        self.assertEqual(result["status"], "confirmed")
        fields = result["vault_record"]["confirmed_fields"]
        self.assertEqual(fields["date_of_birth"]["value"], truth["Date of birth"])
        self.assertEqual(fields["aadhaar_number"]["value"], "XXXX XXXX " + truth["Aadhaar"][-4:])
        self.assertNotIn(truth["Aadhaar"], json.dumps(result, default=str))

    def test_blurred_income_photo_goes_to_review(self, _):
        result, _ = self.run_page("income_certificate__blur.png", "income_certificate")
        self.assertEqual(result["status"], "needs_review")
        self.assertTrue(any("low confidence (0.85)" in flag for flag in result["review_flags"]))


if __name__ == "__main__":
    unittest.main()
