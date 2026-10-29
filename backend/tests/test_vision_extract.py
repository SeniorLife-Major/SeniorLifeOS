"""Tests for medical documents read by the vision model (tools/vision_extract.py + the
vision_extract graph step, Decision #41).

No network: httpx's MockTransport answers instead of Groq and records each request.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2

from backend.graphs.document_graph import build_document_graph
from backend.tools import cloud_ocr
from backend.tools.cloud_ocr import GROQ_CHAT_URL, GROQ_DEFAULT_MODEL, CloudOcrError
from backend.tools.vision_extract import (
    HEALTH_ID_MASK, extract_with_vision, fields_from_answer, health_id_boxes, prompt_for,
)
from test_cloud_ocr import FAKE_KEY, Busy, Recorder, groq_answer, page_with_qr
from test_graph_ocr import AADHAAR, ocr_page, upload_state
from test_ocr import UNIT, ensure_fixtures
from test_privacy import FAKE_HASH_KEY, hash_key_env

_no_env_file = patch("backend.tools.cloud_ocr.find_env_file", return_value=None)
_no_wait = patch("backend.tools.cloud_ocr.cloud_limiter", lambda service: MagicMock())  # acquire() never waits


def setUpModule():
    _no_env_file.start()  # the developer's own .env must not decide these tests
    _no_wait.start()  # fake requests need no spacing


def tearDownModule():
    _no_wait.stop()
    _no_env_file.stop()


ABHA = "91-5023-4458-1290"  # made up, shaped like an ABHA number
PRESCRIPTION = {
    "name": "Meera Rao",
    "doctor_name": "Dr. Anil Kumar",
    "prescription_date": "01/09/2026",
    "medicines": [{"name": "Tab Metformin", "form": "tablet", "strength": "500 mg", "frequency": "1-0-1",
                   "duration": "30 days"}],
}


def answer(fields, before=""):
    return groq_answer(before + json.dumps(fields))


class AnswerTests(unittest.TestCase):
    def test_only_known_fields_and_non_empty_values_are_kept(self):
        text = "<think>the page shows...</think>Here it is: " + json.dumps({
            "name": " Meera   Rao ", "doctor_name": "null", "prescription_date": None, "clinic": "City Clinic",
            "medicines": [{"name": "Tab Metformin", "form": None, "strength": "500 mg", "colour": "white"},
                          {"name": None, "form": None}, "not a row"],
        }) + " Hope this helps."
        self.assertEqual(fields_from_answer(text, "prescription"), {
            "name": "Meera Rao",
            "medicines": [{"name": "Tab Metformin", "form": None, "strength": "500 mg", "frequency": None,
                           "duration": None}],
        })

    def test_id_numbers_the_model_gives_anyway_are_masked(self):
        text = json.dumps({"name": f"Meera Rao {AADHAAR}", "lab_name": f"City Lab ABHA {ABHA}",
                           "report_date": "meera.rao@abdm",
                           "tests": [{"name": "HbA1c", "value": "6.1", "unit": "%", "ref_range": "4-5.6"}]})
        fields = fields_from_answer(text, "lab_report")
        self.assertNotIn(AADHAAR, json.dumps(fields))
        self.assertIn("XXXX XXXX 7973", fields["name"])
        self.assertEqual(fields["lab_name"], f"City Lab ABHA {HEALTH_ID_MASK}")
        self.assertEqual(fields["report_date"], HEALTH_ID_MASK)

    def test_an_answer_that_is_not_a_json_object_is_an_error(self):
        for text in ("Sorry, I can't read this.", "[1, 2]", "{not json}"):
            with self.subTest(text=text), self.assertRaises(CloudOcrError):
                fields_from_answer(text, "medical_bill")

    def test_the_prompt_asks_for_exact_copies_and_no_id_numbers(self):
        prompt = prompt_for("medical_bill")
        for words in ("total_amount", "items", "never guess", "exactly", "null", "Never include ID numbers"):
            self.assertIn(words, prompt)


class HealthIdTests(unittest.TestCase):
    def test_lines_with_an_abha_number_address_or_label_are_found(self):
        page = ocr_page([("Name: Meera Rao", 0.9), (f"ABHA No. {ABHA}", 0.9), ("meera.rao@abdm", 0.9),
                         ("Health ID", 0.9), ("Total: 1,250.00", 0.9)])
        self.assertEqual(health_id_boxes(page), [line["box"] for line in page["lines"][1:4]])


class RequestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_fixtures()

    def test_one_request_with_the_key_in_a_header_and_ids_blacked_out(self):
        page = ocr_page([("Name: Meera Rao", 0.6), ("Dr. Anil Kumar", 0.9), (f"Aadhaar {AADHAAR}", 0.99),
                         (f"ABHA {ABHA}", 0.9), ("Tab Metformin 500 mg", 0.5)])
        recorder = Recorder(answer(PRESCRIPTION))
        fields = extract_with_vision(str(UNIT / "sharp.png"), "image/png", [page], {}, "prescription", FAKE_KEY,
                                     recorder.client())
        self.assertEqual(fields["doctor_name"], "Dr. Anil Kumar")
        request = recorder.requests[0]
        self.assertEqual(str(request.url), GROQ_CHAT_URL)
        self.assertEqual(request.headers["authorization"], f"Bearer {FAKE_KEY}")
        self.assertNotIn(FAKE_KEY, request.content.decode())
        body = json.loads(request.content)
        self.assertEqual((body["model"], body["temperature"]), (GROQ_DEFAULT_MODEL, 0))
        self.assertEqual(body["messages"][0]["content"][0]["text"], prompt_for("prescription"))
        sent = recorder.sent_image()
        self.assertTrue((sent[80:110, 10:600] < 40).all())   # the Aadhaar line
        self.assertTrue((sent[120:150, 10:600] < 40).all())  # the ABHA line
        self.assertFalse((sent[0:30, 10:600] < 40).all())    # the name line is sent as it is

    def test_a_field_found_locally_at_an_id_box_is_blacked_out_too(self):
        page = ocr_page([("Name: Meera Rao", 0.6)])
        recorder = Recorder(answer(PRESCRIPTION))
        extract_with_vision(str(UNIT / "sharp.png"), "image/png", [page], {1: [[10, 200, 600, 230]]},
                            "prescription", FAKE_KEY, recorder.client())
        self.assertTrue((recorder.sent_image()[200:230, 10:600] < 40).all())

    def test_qr_codes_are_blacked_out(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bill.png"
            cv2.imwrite(str(path), cv2.cvtColor(page_with_qr(), cv2.COLOR_RGB2BGR))
            recorder = Recorder(answer({"name": "Meera Rao"}))
            extract_with_vision(str(path), "image/png", [ocr_page([("Bill", 0.9)])], {}, "medical_bill", FAKE_KEY,
                                recorder.client())
        sent = recorder.sent_image()
        self.assertTrue((sent[200:300, 650:750] < 40).all())  # the middle of the QR code
        self.assertEqual(cv2.QRCodeDetector().detectAndDecode(sent)[0], "")

    def test_more_than_three_pages_or_another_type_sends_nothing(self):
        recorder = Recorder(answer(PRESCRIPTION))
        for pages, document_type in (([ocr_page([("x", 0.9)])] * 4, "prescription"),
                                     ([ocr_page([("x", 0.9)])], "income_certificate")):
            with self.subTest(document_type=document_type), self.assertRaises(CloudOcrError):
                extract_with_vision(str(UNIT / "sharp.png"), "image/png", pages, {}, document_type, FAKE_KEY,
                                    recorder.client())
        self.assertEqual(recorder.requests, [])

    def test_failures_never_show_the_answer_body(self):
        recorder = Recorder({"error": {"message": f"echo {AADHAAR}"}}, status=429)
        with self.assertRaises(CloudOcrError) as caught, patch("backend.tools.cloud_ocr.time.sleep"):
            extract_with_vision(str(UNIT / "sharp.png"), "image/png", [ocr_page([("x", 0.9)])], {}, "lab_report",
                                FAKE_KEY, recorder.client())
        self.assertEqual(str(caught.exception), "the vision model failed (HTTP 429)")


    def test_a_busy_model_is_waited_out_and_tried_again(self):
        recorder = Busy(answer(PRESCRIPTION), [{"retry-after": "3"}])
        with patch("backend.tools.cloud_ocr.time.sleep") as sleep:
            found = extract_with_vision(str(UNIT / "sharp.png"), "image/png", [ocr_page([("x", 0.9)])], {},
                                        "prescription", FAKE_KEY, recorder.client())
        self.assertEqual(len(recorder.requests), 2)
        sleep.assert_called_once_with(3.0)
        self.assertEqual(found["name"], PRESCRIPTION["name"])


@patch("backend.utils.privacy.find_env_file", return_value=None)
class GraphVisionTests(unittest.TestCase):
    LOCAL = [("Patient: Meera Ra0", 0.62), ("Doctor: Dr. Anil Kumar", 0.95), ("Date: 01/09/2026", 0.95),
             (f"Aadhaar: {AADHAAR}", 0.99), (f"ABHA {ABHA}", 0.9), ("Tab Metf0rmin 500mg 1-0-1", 0.4)]
    CONSENT = {"cloud_ocr_consent": True, "medical_cloud_consent": True}
    OWNER_CONFIRMS = {"review_decision": {"action": "confirm", "reviewer_id": "senior-meera"}}

    @classmethod
    def setUpClass(cls):
        ensure_fixtures()

    def run_upload(self, recorder, key=FAKE_KEY, pages=None, doc_type="prescription", **extra):
        state = upload_state(UNIT / "sharp.png", "image/png", doc_type, **extra)
        pages = pages or [ocr_page(self.LOCAL)]
        with patch("backend.graphs.document_graph.read_document", return_value=pages), \
                patch("backend.tools.cloud_ocr.httpx.Client", lambda **_: recorder.client()), \
                patch.dict(os.environ, {}), hash_key_env(FAKE_HASH_KEY):
            for name in (cloud_ocr.KEY_NAME, cloud_ocr.GROQ_KEY_NAME, cloud_ocr.ENGINE_SETTING,
                         cloud_ocr.GROQ_MODEL_SETTING):
                os.environ.pop(name, None)  # never the developer's own settings
            if key:
                os.environ[cloud_ocr.GROQ_KEY_NAME] = key
            return build_document_graph().invoke(state)

    def test_with_consent_the_vision_values_are_used_and_all_reviewed(self, _):
        recorder = Recorder(answer(PRESCRIPTION))
        result = self.run_upload(recorder, **self.CONSENT)
        self.assertEqual(len(recorder.requests), 1)
        self.assertIn("vision_extract:found_4", result["events"])
        self.assertFalse(any(event.startswith("cloud_ocr") for event in result["events"]))  # asked only once
        fields = result["extracted_fields"]
        for key in PRESCRIPTION:
            self.assertEqual((fields[key]["source"], fields[key]["confidence"]), ("vision_model", 0.0))
            self.assertIn(f"{key}: read by the AI vision model from the picture, please check", result["review_flags"])
        self.assertEqual(result["normalized_fields"]["name"], "Meera Rao")
        self.assertEqual(result["normalized_fields"]["prescription_date"], "2026-09-01")
        self.assertEqual(result["normalized_fields"]["medicines"][0]["strength"], "500 mg")
        self.assertEqual(result["status"], "needs_review")

    def test_a_field_local_ocr_read_differently_is_flagged_and_the_same_one_is_not(self, _):
        result = self.run_upload(Recorder(answer(PRESCRIPTION)), **self.CONSENT)
        self.assertIn("name: the local reading differs, please check", result["review_flags"])
        self.assertNotIn("doctor_name: the local reading differs, please check", result["review_flags"])
        self.assertNotIn("prescription_date: the local reading differs, please check", result["review_flags"])
        self.assertIn("name", result["local_disagreements"])

    def test_without_both_consents_nothing_is_sent(self, _):
        for consent in ({}, {"cloud_ocr_consent": True}, {"medical_cloud_consent": True}):
            with self.subTest(consent=consent):
                recorder = Recorder(answer(PRESCRIPTION))
                result = self.run_upload(recorder, **consent)
                self.assertEqual(recorder.requests, [])
                self.assertFalse(any(event.startswith("vision_extract") for event in result["events"]))
                self.assertEqual(result["extracted_fields"]["name"]["value"], "Meera Ra0")

    def test_other_document_types_never_use_it(self, _):
        recorder = Recorder(answer(PRESCRIPTION))
        result = self.run_upload(recorder, doc_type="income_certificate", **self.CONSENT)
        self.assertFalse(any(event.startswith("vision_extract") for event in result["events"]))

    def test_no_key_or_a_failure_keeps_the_local_reading_and_asks_once(self, _):
        cases = ((Recorder(answer(PRESCRIPTION)), None, "vision_extract:no_key",
                  "Vision reading skipped: GROQ_API_KEY is not set"),
                 (Recorder({"error": "x"}, status=500), FAKE_KEY, "vision_extract:failed",
                  "Vision reading unavailable (the vision model failed (HTTP 500))"),
                 (Recorder(groq_answer("I cannot help with that.")), FAKE_KEY, "vision_extract:failed",
                  "Vision reading unavailable (The vision model sent an answer that could not be read)"),
                 (Recorder(answer({"name": None})), FAKE_KEY, "vision_extract:no_fields",
                  "The vision model found no fields"))
        for recorder, key, event, warning in cases:
            with self.subTest(event=event, warning=warning):
                result = self.run_upload(recorder, key=key, **self.CONSENT)
                self.assertIn(event, result["events"])
                self.assertIn(warning, result["warnings"])
                self.assertLessEqual(len(recorder.requests), 1)
                self.assertFalse(any(e.startswith("cloud_ocr") for e in result["events"]))
                self.assertEqual(result["extracted_fields"]["name"]["value"], "Meera Ra0")
                self.assertEqual(result["status"], "needs_review")
                self.assertNotIn(FAKE_KEY, json.dumps(result, default=str))

    def test_a_handwritten_page_ocr_reads_badly_goes_to_the_vision_model(self, _):
        scrawl = [ocr_page([("Pt Meera", 0.3), ("Tab Metf 500", 0.2)], quality=0.25)]
        recorder = Recorder(answer(PRESCRIPTION))
        result = self.run_upload(recorder, pages=scrawl, **self.CONSENT)
        self.assertEqual(len(recorder.requests), 1)
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["normalized_fields"]["doctor_name"], "Dr. Anil Kumar")
        # Without consent, or if the model fails, such a page is sent back as before.
        for extra, key, event in (({}, FAKE_KEY, "extract_content:unreadable"),
                                  (self.CONSENT, None, "vision_extract:no_key")):
            with self.subTest(event=event):
                result = self.run_upload(Recorder(answer(PRESCRIPTION)), key=key, pages=scrawl, **extra)
                self.assertEqual(result["status"], "needs_reupload")
                self.assertIn(event, result["events"])

    def test_a_blurry_photo_is_still_sent_back(self, _):
        blurry = [ocr_page([("Pt Meera", 0.3)], blurry=True, quality=0.3)]
        recorder = Recorder(answer(PRESCRIPTION))
        result = self.run_upload(recorder, pages=blurry, **self.CONSENT)
        self.assertEqual(recorder.requests, [])  # its ID numbers could not be found to black them out
        self.assertEqual(result["status"], "needs_reupload")

    def test_a_pdf_with_stored_text_is_read_locally_only(self, _):
        page = {**ocr_page(self.LOCAL), "engine": "pdf_text"}
        recorder = Recorder(answer(PRESCRIPTION))
        result = self.run_upload(recorder, pages=[page], **self.CONSENT)
        self.assertEqual(recorder.requests, [])
        self.assertFalse(any(event.startswith("vision_extract") for event in result["events"]))

    def test_more_than_three_pages_keep_the_local_reading(self, _):
        recorder = Recorder(answer(PRESCRIPTION))
        pages = [{**ocr_page(self.LOCAL), "page": number} for number in range(1, 5)]
        result = self.run_upload(recorder, pages=pages, **self.CONSENT)
        self.assertEqual(recorder.requests, [])
        self.assertIn("Vision reading unavailable (more than 3 pages)", result["warnings"])

    def test_confirmed_vision_values_are_saved_and_no_id_number_is_left(self, _):
        recorder = Recorder(answer({**PRESCRIPTION, "aadhaar_number": AADHAAR}))
        result = self.run_upload(recorder, **self.CONSENT, **self.OWNER_CONFIRMS)
        self.assertEqual(result["status"], "confirmed")
        confirmed = result["vault_record"]["confirmed_fields"]
        self.assertEqual(confirmed["name"]["value"], "Meera Rao")
        self.assertEqual(confirmed["name"]["source_ref"], "document:doc-upload-001:vision_model")
        # The ID number comes from local OCR (and is masked), never from the model.
        self.assertEqual(result["extracted_fields"]["aadhaar_number"]["source_ref"], "page:1:line:4")
        self.assertEqual(confirmed["aadhaar_number"]["value"], "XXXX XXXX 7973")
        self.assertNotIn(AADHAAR, json.dumps(result, default=str))


if __name__ == "__main__":
    unittest.main()
