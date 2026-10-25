"""Tests: ID numbers are masked in page text even when no field was extracted for them."""

import json
import unittest
from unittest.mock import patch

from backend.graphs.document_graph import build_document_graph, mask_pages
from backend.utils.privacy import mask_ids_in_text
from test_graph_ocr import AADHAAR, ocr_page, upload_state
from test_ocr import UNIT, ensure_fixtures
from test_privacy import FAKE_HASH_KEY, hash_key_env

HARD_RESULTS = UNIT.parent / "ocr_hard" / "results_paddle.json"
HARD_MANIFEST = UNIT.parent / "ocr_hard" / "manifest.json"
PAN = "HAVPC1814K"


class MaskIdsInTextTests(unittest.TestCase):
    def test_unlabelled_aadhaar_in_any_spacing(self):
        for raw in (AADHAAR, AADHAAR.replace(" ", ""), AADHAAR.replace(" ", "-")):
            with self.subTest(raw=raw):
                self.assertEqual(mask_ids_in_text(f"SAMPLE CARD\n{raw}\nMALE"), "SAMPLE CARD\nXXXX XXXX 7973\nMALE")

    def test_twelve_digits_that_fail_the_checksum_are_left_alone(self):
        text = "Ref 3399 8698 7974 and phone-like 123456789012"
        self.assertEqual(mask_ids_in_text(text), text)

    def test_pan_anywhere(self):
        self.assertEqual(mask_ids_in_text(f"Card {PAN} issued"), "Card XXXXXX814K issued")
        self.assertEqual(mask_ids_in_text("Code ABCXE1234F"), "Code ABCXE1234F")  # 4th letter not a holder type

    def test_labelled_account_and_policy_numbers(self):
        self.assertEqual(mask_ids_in_text("A/c No: 44719041229"), "A/c No: XXXXXXX1229")
        self.assertEqual(mask_ids_in_text("Policy No: HLT/2026/004512"), "Policy No: XXXXXXXXXXX4512")

    def test_unlabelled_account_like_numbers_are_left_alone(self):
        text = "Balance 44719041229.00 on 03/04/26"
        self.assertEqual(mask_ids_in_text(text), text)

    def test_masking_twice_changes_nothing(self):
        once = mask_ids_in_text(f"{AADHAAR} {PAN} A/c No: 44719041229")
        self.assertEqual(mask_ids_in_text(once), once)


@patch("backend.utils.privacy.find_env_file", return_value=None)
class GraphUnlabelledIdTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_fixtures()

    def run_upload(self, page, doc_type, key=FAKE_HASH_KEY, **extra):
        with patch("backend.graphs.document_graph.read_document", return_value=[page]), hash_key_env(key):
            return build_document_graph().invoke(upload_state(UNIT / "sharp.png", "image/png", doc_type, **extra))

    def test_unlabelled_aadhaar_on_an_id_card_is_masked(self, _):
        page = ocr_page([("SAMPLE IDENTITY CARD", 0.99), ("Name: Meera Rao", 0.99),
                         ("DOB: 17/09/1950", 0.99), (AADHAAR, 0.99)])
        result = self.run_upload(page, "identity")
        self.assertEqual(result["status"], "confirmed")
        self.assertIn("protect_identifiers:none", result["events"])  # no ID *field* was extracted
        self.assertIn("XXXX XXXX 7973", result["document"]["pages"][0]["text"])
        self.assertEqual(result["document"]["pages"][0]["lines"][3]["text"], "XXXX XXXX 7973")
        self.assertNotIn(AADHAAR, json.dumps(result, default=str))

    def test_masking_needs_no_hash_key_when_no_id_field_is_stored(self, _):
        page = ocr_page([("Name: Meera Rao", 0.99), ("DOB: 17/09/1950", 0.99), (AADHAAR, 0.99)])
        result = self.run_upload(page, "identity", key=None)
        self.assertEqual(result["status"], "confirmed")
        self.assertNotIn(AADHAAR, json.dumps(result, default=str))

    def test_unlabelled_pan_next_to_a_labelled_aadhaar(self, _):
        page = ocr_page([("Name: Meera Rao", 0.99), ("DOB: 17/09/1950", 0.99), (f"Aadhaar: {AADHAAR}", 0.99),
                         (PAN, 0.99)])
        result = self.run_upload(page, "identity")
        dumped = json.dumps(result, default=str)
        self.assertNotIn(AADHAAR, dumped)
        self.assertNotIn(PAN, dumped)

    def test_pieces_are_dropped_even_without_id_fields(self, _):
        page = ocr_page([("Name: Meera Rao", 0.99), ("DOB: 17/09/1950", 0.99)])
        for line in page["lines"]:
            line["parts"] = [{"text": line["text"], "confidence": 0.99, "box": line["box"]}]
        result = self.run_upload(page, "identity")
        self.assertFalse(any("parts" in line for line in result["document"]["pages"][0]["lines"]))

    def test_real_ocr_output_of_the_hard_id_card(self, _):
        # The masking step itself, on what PaddleOCR really returned for the hard ID card.
        # (The whole graph can't publish this card yet: its name has no label, so the run
        # stops at review before masking. See Decision #27.)
        if not HARD_RESULTS.exists():
            self.skipTest("hard dataset results missing (run tests/make_hard_dataset.py --score)")
        saved = {p["path"]: p for p in json.loads(HARD_RESULTS.read_text(encoding="utf-8"))["pages"]}
        manifest = {e["path"]: e for e in json.loads(HARD_MANIFEST.read_text(encoding="utf-8"))}
        for path in ("pages/identity__scan.png", "pages/identity__phone_photo.png"):
            with self.subTest(path=path):
                ocr = saved[path]
                aadhaar = manifest[path]["fields"]["aadhaar_number"]["shown"]
                self.assertIn(aadhaar, ocr["text"])  # the full number really is in the OCR text
                document = {"pages": [{"page": 1, "text": ocr["text"], "lines": ocr["lines"]}]}
                dumped = json.dumps(mask_pages(document, {}))
                self.assertNotIn(aadhaar, dumped)
                self.assertNotIn(aadhaar.replace(" ", ""), dumped)
                self.assertIn("XXXX XXXX " + aadhaar[-4:], dumped)


if __name__ == "__main__":
    unittest.main()
