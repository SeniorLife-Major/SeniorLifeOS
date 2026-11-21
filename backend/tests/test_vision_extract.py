"""Tests for documents read by the vision model (tools/vision_extract.py + the vision_extract and
classify_document graph steps, Decision #41). Photos are not read on this machine: the vision
model is the only reader of photos and scanned pages, ID numbers included.

No network: httpx's MockTransport answers instead of Groq and records each request.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2
import httpx
from PIL import Image

from backend.graphs.document_graph import build_document_graph
from backend.tools import cloud_ocr
from backend.tools.cloud_ocr import GROQ_CHAT_URL, GROQ_DEFAULT_MODEL, CloudOcrError
from backend.tools.ocr import IMAGE_ENGINE, read_document
from backend.tools.vision_extract import (
    HEALTH_ID_MASK, LABELS, SHAPES, classify_prompt, classify_with_vision, extract_with_vision, fields_from_answer,
    prompt_for, type_from_answer,
)
from backend.tools.document_rules import REQUIRED_FIELDS
from backend.utils.privacy import ID_FIELDS
from test_cloud_ocr import FAKE_KEY, REAL_CLIENT, Busy, Recorder, groq_answer, page_with_qr
from test_graph_ocr import AADHAAR, ocr_page, upload_state
from test_ocr import UNIT, ensure_fixtures
from test_privacy import FAKE_HASH_KEY, hash_key_env

_no_env_file = patch("backend.tools.cloud_ocr.find_env_file", return_value=None)
_no_wait = patch("backend.tools.cloud_ocr.cloud_limiter", lambda service: MagicMock())  # acquire() never waits


def setUpModule():
    _no_env_file.start()  # the developer's own .env must not decide these tests
    _no_wait.start()  # fake requests need no spacing
    ensure_fixtures()
    if not FOUR_PAGES.exists():
        with Image.open(UNIT / "sharp.png") as picture:
            picture.save(FOUR_PAGES, "PDF", resolution=100, save_all=True, append_images=[picture] * 3)


def tearDownModule():
    _no_wait.stop()
    _no_env_file.stop()


FOUR_PAGES = UNIT / "scanned_four_pages.pdf"
ABHA = "91-5023-4458-1290"  # made up, shaped like an ABHA number
PAN = "HAVPC1814K"  # made up, valid shape
ACCOUNT = "123456789012"  # made up
PRESCRIPTION = {
    "name": "Meera Rao",
    "doctor_name": "Dr. Anil Kumar",
    "prescription_date": "01/09/2026",
    "medicines": [{"name": "Tab Metformin", "form": "tablet", "strength": "500 mg", "frequency": "1-0-1",
                   "duration": "30 days"}],
}


def answer(fields, before=""):
    return groq_answer(before + json.dumps(fields))


class Answers(Recorder):
    """Answers each request with the next of several answers (e.g. the type, then the fields)."""

    def __init__(self, *answers):
        super().__init__()
        self.answers = list(answers)

    def __call__(self, request):
        self.requests.append(request)
        return httpx.Response(200, json=self.answers.pop(0))


class ShapeTests(unittest.TestCase):
    def test_every_type_and_every_required_field_is_asked_for_id_numbers_too(self):
        self.assertEqual(set(SHAPES), set(REQUIRED_FIELDS))
        self.assertEqual(set(LABELS), set(SHAPES))
        for document_type, shape in SHAPES.items():
            with self.subTest(document_type=document_type):
                self.assertEqual(REQUIRED_FIELDS[document_type] - set(shape), set())
        self.assertEqual(set(SHAPES["identity"]) & set(ID_FIELDS), {"aadhaar_number", "pan_number"})
        self.assertEqual(set(SHAPES["health_insurance"]) & set(ID_FIELDS), {"policy_number"})
        self.assertEqual(set(SHAPES["bank_passbook"]) & set(ID_FIELDS), {"account_number"})
        self.assertEqual(set(SHAPES["death_certificate"]) & set(ID_FIELDS), {"registration_number"})
        self.assertEqual(set(SHAPES["ration_card"]) & set(ID_FIELDS), {"ration_card_number"})
        numbered = {"identity", "health_insurance", "bank_passbook", "death_certificate", "ration_card"}
        for document_type in set(SHAPES) - numbered:
            self.assertFalse(set(SHAPES[document_type]) & set(ID_FIELDS), document_type)

    def test_an_asked_for_id_number_is_kept_as_given_for_the_graph_to_check_and_mask(self):
        found = fields_from_answer(json.dumps({"name": "Meera Rao", "aadhaar_number": AADHAAR, "pan_number": PAN}),
                                   "identity")
        self.assertEqual(found, {"name": "Meera Rao", "aadhaar_number": AADHAAR, "pan_number": PAN})

    def test_an_id_number_this_type_does_not_ask_for_is_dropped(self):
        found = fields_from_answer(json.dumps({"name": "Meera Rao", "aadhaar_number": AADHAAR}), "prescription")
        self.assertEqual(found, {"name": "Meera Rao"})


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

    def test_id_numbers_in_other_fields_are_masked(self):
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
            with self.subTest(text=text, step="type"), self.assertRaises(CloudOcrError):
                type_from_answer(text)

    def test_the_prompt_asks_for_exact_copies_and_no_other_id_numbers(self):
        prompt = prompt_for("medical_bill")
        for words in ("total_amount", "items", "never guess", "exactly", "null",
                      "Never include an ID number", "except in a field above that asks for it"):
            self.assertIn(words, prompt)
        self.assertIn("aadhaar_number", prompt_for("identity"))

    def test_the_type_answer_must_name_one_of_our_types(self):
        self.assertEqual(type_from_answer('{"document_type": "lab_report"}'), "lab_report")
        for text in ('{"document_type": null}', '{"document_type": "passport"}', '{"type": "lab_report"}',
                     '{"document_type": ["lab_report"]}'):
            with self.subTest(text=text):
                self.assertIsNone(type_from_answer(text))

    def test_the_type_prompt_lists_every_type_and_asks_for_json(self):
        prompt = classify_prompt()
        for document_type in REQUIRED_FIELDS:
            self.assertIn(f'"{document_type}"', prompt)
        self.assertIn("JSON", prompt)


class RequestTests(unittest.TestCase):
    def test_one_request_with_the_key_in_a_header_and_the_picture_in_the_message(self):
        recorder = Recorder(answer(PRESCRIPTION))
        fields = extract_with_vision(str(UNIT / "sharp.png"), "image/png", "prescription", FAKE_KEY, recorder.client())
        self.assertEqual(fields["doctor_name"], "Dr. Anil Kumar")
        self.assertEqual(len(recorder.requests), 1)
        request = recorder.requests[0]
        self.assertEqual(str(request.url), GROQ_CHAT_URL)
        self.assertEqual(request.headers["authorization"], f"Bearer {FAKE_KEY}")
        self.assertNotIn(FAKE_KEY, str(request.url) + request.content.decode())
        body = json.loads(request.content)
        self.assertEqual((body["model"], body["temperature"]), (GROQ_DEFAULT_MODEL, 0))
        self.assertEqual(body["response_format"], {"type": "json_object"})  # Groq's JSON mode
        self.assertIn("JSON", body["messages"][0]["content"][0]["text"])  # JSON mode needs the word in the prompt
        self.assertEqual(body["messages"][0]["content"][0]["text"], prompt_for("prescription"))
        self.assertTrue(body["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        sent = recorder.sent_image()
        self.assertFalse((sent[60:100, 60:400] < 40).all())  # the text is sent as it is: nothing finds IDs now

    def test_qr_codes_are_blacked_out(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bill.png"
            cv2.imwrite(str(path), cv2.cvtColor(page_with_qr(), cv2.COLOR_RGB2BGR))
            recorder = Recorder(answer({"name": "Meera Rao"}))
            extract_with_vision(str(path), "image/png", "medical_bill", FAKE_KEY, recorder.client())
        sent = recorder.sent_image()
        self.assertTrue((sent[200:300, 650:750] < 40).all())  # the middle of the QR code
        self.assertEqual(cv2.QRCodeDetector().detectAndDecode(sent)[0], "")

    def test_large_pages_are_shrunk_before_sending(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "big.png"
            Image.new("RGB", (1500, 3000), "white").save(path)
            recorder = Recorder(answer({"name": "Meera Rao"}))
            extract_with_vision(str(path), "image/png", "medical_bill", FAKE_KEY, recorder.client())
        self.assertEqual(recorder.sent_image().shape[:2], (cloud_ocr.GROQ_MAX_SIDE, 1000))

    def test_every_page_of_a_scan_goes_in_the_one_request(self):
        recorder = Recorder(answer(PRESCRIPTION))
        extract_with_vision(str(UNIT / "scanned_two_pages.pdf"), "application/pdf", "prescription", FAKE_KEY,
                            recorder.client())
        content = json.loads(recorder.requests[0].content)["messages"][0]["content"]
        self.assertEqual([part["type"] for part in content], ["text", "image_url", "image_url"])

    def test_more_than_three_pages_or_another_type_sends_nothing(self):
        recorder = Recorder(answer(PRESCRIPTION))
        for path, mime_type, document_type in ((FOUR_PAGES, "application/pdf", "prescription"),
                                               (UNIT / "sharp.png", "image/png", "passport")):  # not a type of ours
            with self.subTest(document_type=document_type), self.assertRaises(CloudOcrError):
                extract_with_vision(str(path), mime_type, document_type, FAKE_KEY, recorder.client())
        with self.assertRaises(CloudOcrError):
            classify_with_vision(str(FOUR_PAGES), "application/pdf", FAKE_KEY, recorder.client())
        self.assertEqual(recorder.requests, [])

    def test_failures_never_show_the_answer_body(self):
        recorder = Recorder({"error": {"message": f"echo {AADHAAR}"}}, status=429)
        with self.assertRaises(CloudOcrError) as caught, patch("backend.tools.cloud_ocr.time.sleep"):
            extract_with_vision(str(UNIT / "sharp.png"), "image/png", "lab_report", FAKE_KEY, recorder.client())
        self.assertEqual(str(caught.exception), "the vision model failed (HTTP 429)")
        for recorder in (Recorder({"choices": []}), Recorder(groq_answer(None))):
            with self.assertRaises(CloudOcrError) as caught:
                extract_with_vision(str(UNIT / "sharp.png"), "image/png", "lab_report", FAKE_KEY, recorder.client())
            self.assertEqual(str(caught.exception), "The vision model sent an answer that could not be read")

    def test_a_network_error_becomes_a_vision_error(self):
        def refuse(request):
            raise httpx.ConnectError("no route")
        client = REAL_CLIENT(transport=httpx.MockTransport(refuse))
        with self.assertRaises(CloudOcrError) as caught:
            extract_with_vision(str(UNIT / "sharp.png"), "image/png", "lab_report", FAKE_KEY, client)
        self.assertEqual(str(caught.exception), "the vision model could not be reached (ConnectError)")

    def test_a_reply_json_mode_refused_never_shows_what_the_model_wrote(self):
        # In JSON mode Groq answers 400 json_validate_failed when the model's reply isn't valid JSON,
        # and that answer repeats the reply, which may hold what was read on the page.
        recorder = Recorder({"error": {"message": "Failed to generate JSON.", "type": "invalid_request_error",
                                       "code": "json_validate_failed",
                                       "failed_generation": f"Meera Rao, Aadhaar {AADHAAR}"}}, status=400)
        with self.assertRaises(CloudOcrError) as caught:
            extract_with_vision(str(UNIT / "sharp.png"), "image/png", "prescription", FAKE_KEY, recorder.client())
        self.assertEqual(str(caught.exception), "the vision model failed (HTTP 400)")
        self.assertEqual(len(recorder.requests), 1)  # not tried again

    def test_a_busy_model_is_waited_out_and_tried_again(self):
        recorder = Busy(answer(PRESCRIPTION), [{"retry-after": "3"}])
        with patch("backend.tools.cloud_ocr.time.sleep") as sleep:
            found = extract_with_vision(str(UNIT / "sharp.png"), "image/png", "prescription", FAKE_KEY,
                                        recorder.client())
        self.assertEqual(len(recorder.requests), 2)
        sleep.assert_called_once_with(3.0)
        self.assertEqual(found["name"], PRESCRIPTION["name"])

    def test_the_type_is_asked_with_its_own_prompt(self):
        recorder = Recorder(answer({"document_type": "bank_passbook"}))
        found = classify_with_vision(str(UNIT / "sharp.png"), "image/png", FAKE_KEY, recorder.client())
        self.assertEqual(found, "bank_passbook")
        body = json.loads(recorder.requests[0].content)
        self.assertEqual(body["messages"][0]["content"][0]["text"], classify_prompt())
        self.assertEqual(body["response_format"], {"type": "json_object"})


@patch("backend.utils.privacy.find_env_file", return_value=None)
class GraphVisionTests(unittest.TestCase):
    """Real photos through the graph: read_document is not replaced, so nothing is read locally."""

    CONSENT = {"cloud_ocr_consent": True, "medical_cloud_consent": True}
    OWNER_CONFIRMS = {"review_decision": {"action": "confirm", "reviewer_id": "senior-meera"}}

    def run_upload(self, recorder, key=FAKE_KEY, doc_type="prescription", name="sharp.png", pages=None,
                   mime_type="image/png", **extra):
        state = upload_state(UNIT / name, mime_type, doc_type, **extra)
        reader = patch("backend.graphs.document_graph.read_document", return_value=pages) if pages else None
        with patch("backend.tools.vision_extract.httpx.Client", lambda **_: recorder.client()), \
                patch.dict(os.environ, {}), hash_key_env(FAKE_HASH_KEY):
            for name_ in (cloud_ocr.GROQ_KEY_NAME, cloud_ocr.GROQ_MODEL_SETTING):
                os.environ.pop(name_, None)  # never the developer's own settings
            if key:
                os.environ[cloud_ocr.GROQ_KEY_NAME] = key
            if reader:
                with reader:
                    return build_document_graph().invoke(state)
            return build_document_graph().invoke(state)

    def test_with_consent_the_vision_values_are_used_and_all_reviewed(self, _):
        recorder = Recorder(answer(PRESCRIPTION))
        result = self.run_upload(recorder, **self.CONSENT)
        self.assertEqual(len(recorder.requests), 1)
        self.assertIn("read_file:image", result["events"])
        self.assertIn("vision_extract:found_4", result["events"])
        fields = result["extracted_fields"]
        for key in PRESCRIPTION:
            self.assertEqual((fields[key]["source"], fields[key]["confidence"]), ("vision_model", 0.0))
            self.assertIn(f"{key}: read by the AI vision model from the picture, please check", result["review_flags"])
        self.assertEqual(result["normalized_fields"]["name"], "Meera Rao")
        self.assertEqual(result["normalized_fields"]["prescription_date"], "2026-09-01")
        self.assertEqual(result["normalized_fields"]["medicines"][0]["strength"], "500 mg")
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual((result["local_disagreements"], result["vision_not_on_page"]), ([], []))  # nothing stored

    def test_without_both_consents_nothing_is_sent_and_the_owner_types_it_in(self, _):
        for consent in ({}, {"cloud_ocr_consent": True}, {"medical_cloud_consent": True}):
            with self.subTest(consent=consent):
                recorder = Recorder(answer(PRESCRIPTION))
                result = self.run_upload(recorder, **consent)
                self.assertEqual(recorder.requests, [])
                self.assertFalse(any(event.startswith("vision_extract") for event in result["events"]))
                self.assertIn("extract_content:photo_not_read", result["events"])
                self.assertEqual(result["status"], "needs_review")
                self.assertEqual(result["extracted_fields"], {})

    def test_an_income_certificate_is_read_with_cloud_consent_alone(self, _):
        recorder = Recorder(answer({"name": "Meera Rao", "annual_income": "Rs. 2,56,000/-", "issue_date": "23/12/2025",
                                    "issuing_authority": None}))
        result = self.run_upload(recorder, doc_type="income_certificate", cloud_ocr_consent=True)
        self.assertEqual(len(recorder.requests), 1)
        self.assertIn("vision_extract:found_3", result["events"])
        self.assertEqual(result["normalized_fields"]["annual_income"], 256000)
        self.assertEqual(result["normalized_fields"]["issue_date"], "2025-12-23")
        self.assertEqual(result["vision_not_on_page"], [])  # no stored text: nothing to look it up in
        self.assertEqual(result["status"], "needs_review")

    def test_an_id_card_is_sent_with_only_face_and_qr_blacked_and_its_number_is_checked_and_masked(self, _):
        recorder = Recorder(answer({"name": "Meera Rao", "date_of_birth": "17/09/1950", "aadhaar_number": AADHAAR}))
        with patch("backend.tools.vision_extract.face_boxes", return_value=[[400, 0, 600, 70]]) as faces:
            result = self.run_upload(recorder, doc_type="identity", cloud_ocr_consent=True, **self.OWNER_CONFIRMS)
        faces.assert_called_once()
        sent = recorder.sent_image()
        self.assertTrue((sent[0:70, 400:600] < 40).all())  # where the face was found
        self.assertEqual(result["status"], "confirmed")
        fields = result["vault_record"]["confirmed_fields"]
        self.assertEqual(fields["name"]["value"], "Meera Rao")
        self.assertEqual(fields["aadhaar_number"]["value"], "XXXX XXXX 7973")
        self.assertEqual(fields["aadhaar_number"]["source_ref"], "document:doc-upload-001:vision_model")
        self.assertIn("aadhaar_number", result["identifier_hashes"])
        self.assertIn("aadhaar_number: read by the AI vision model from the picture, please check",
                      result["review_flags"])
        dumped = json.dumps(result, default=str)
        self.assertNotIn(AADHAAR, dumped)
        self.assertNotIn(AADHAAR.replace(" ", ""), dumped)

    def test_an_id_number_with_a_wrong_check_digit_is_flagged_and_never_left_in_full(self, _):
        wrong = AADHAAR[:-1] + str((int(AADHAAR[-1]) + 1) % 10)
        recorder = Recorder(answer({"name": "Meera Rao", "date_of_birth": "17/09/1950", "aadhaar_number": wrong}))
        result = self.run_upload(recorder, doc_type="identity", cloud_ocr_consent=True)
        self.assertEqual(result["status"], "needs_review")
        self.assertIn("aadhaar_number: failed format or checksum check", result["review_flags"])
        rejected = self.run_upload(Recorder(answer({"name": "Meera Rao", "aadhaar_number": wrong})),
                                   doc_type="identity", cloud_ocr_consent=True,
                                   review_decision={"action": "reject", "reviewer_id": "senior-meera"})
        self.assertEqual(rejected["status"], "rejected")
        self.assertNotIn(wrong, json.dumps(rejected, default=str))  # masked on the way out

    def test_a_passbook_account_number_from_the_model_is_masked_when_saved(self, _):
        recorder = Recorder(answer({"name": "Meera Rao", "bank_name": "State Bank of India", "ifsc": "SBIN0001234",
                                    "account_number": ACCOUNT}))
        result = self.run_upload(recorder, doc_type="bank_passbook", cloud_ocr_consent=True, **self.OWNER_CONFIRMS)
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["vault_record"]["confirmed_fields"]["account_number"]["value"], "XXXXXXXX9012")
        self.assertNotIn(ACCOUNT, json.dumps(result, default=str))

    def test_no_key_or_a_failure_asks_the_owner_to_type_it_in_and_asks_once(self, _):
        cases = ((Recorder(answer(PRESCRIPTION)), None, "extract_content:photo_not_read", "GROQ_API_KEY is not set"),
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
                self.assertTrue(any(warning in text for text in result["warnings"]), result["warnings"])
                self.assertLessEqual(len(recorder.requests), 1)
                self.assertEqual(result["status"], "needs_review")
                self.assertNotIn(FAKE_KEY, json.dumps(result, default=str))

    def test_a_blurred_photo_the_model_could_not_read_is_asked_for_again(self, _):
        result = self.run_upload(Recorder({"error": "x"}, status=500), name="blurred.png", **self.CONSENT)
        self.assertEqual(result["status"], "needs_reupload")
        self.assertIn("vision_extract:failed", result["events"])

    def test_a_blurred_photo_is_still_sent_to_the_model(self, _):
        recorder = Recorder(answer(PRESCRIPTION))
        result = self.run_upload(recorder, name="blurred.png", **self.CONSENT)
        self.assertEqual(len(recorder.requests), 1)
        self.assertEqual(result["status"], "needs_review")

    def test_a_pdf_with_stored_text_is_read_locally_only(self, _):
        recorder = Recorder(answer({"name": "Meera Rao"}))
        result = self.run_upload(recorder, doc_type="health_insurance", name="digital.pdf",
                                 mime_type="application/pdf", **self.CONSENT)
        self.assertEqual(recorder.requests, [])
        self.assertIn("read_file:pdf_text", result["events"])
        self.assertFalse(any(event.startswith("vision_extract") for event in result["events"]))

    def test_more_than_three_pages_are_not_sent_and_the_owner_types_it_in(self, _):
        recorder = Recorder(answer(PRESCRIPTION))
        result = self.run_upload(recorder, name=FOUR_PAGES.name, mime_type="application/pdf", **self.CONSENT)
        self.assertEqual(recorder.requests, [])
        self.assertIn("Vision reading unavailable (more than 3 pages)", result["warnings"])
        self.assertEqual(result["status"], "needs_review")

    def test_a_pdf_with_some_stored_text_is_compared_and_its_id_number_kept(self, _):
        stored = {**ocr_page([("Name: Meera Ra0", 0.99), ("Date of birth: 1950-09-17", 0.99),
                              (f"Aadhaar: {AADHAAR}", 0.99)]), "engine": "pdf_text"}
        picture = {"page": 2, "quality": 1.0, "text": "", "lines": [], "engine": IMAGE_ENGINE, "sharpness": 500.0,
                   "blurry": False}
        other = "4567 8912 3456"
        recorder = Recorder(answer({"name": "Meera Rao", "date_of_birth": "17/09/1950", "aadhaar_number": other,
                                    "pan_number": PAN}))
        result = self.run_upload(recorder, doc_type="identity", pages=[stored, picture], cloud_ocr_consent=True)
        self.assertEqual(len(recorder.requests), 1)
        self.assertIn("name", result["local_disagreements"])
        self.assertIn("aadhaar_number", result["local_disagreements"])
        self.assertIn("name: the local reading differs, please check", result["review_flags"])
        self.assertNotIn("date_of_birth", result["local_disagreements"])
        self.assertEqual(result["extracted_fields"]["aadhaar_number"]["value"], AADHAAR)  # stored text is exact
        self.assertEqual(result["vision_not_on_page"], ["pan_number"])
        self.assertIn("pan_number: not found in the text read from the file, please check carefully",
                      result["review_flags"])

    def test_a_photo_of_unknown_type_is_classified_by_the_model_with_both_consents(self, _):
        recorder = Answers(answer({"document_type": "prescription"}), answer(PRESCRIPTION))
        result = self.run_upload(recorder, doc_type=None, **self.CONSENT)
        self.assertEqual(len(recorder.requests), 2)
        self.assertIn("classify_document:prescription", result["events"])
        self.assertIn("vision_extract:found_4", result["events"])
        self.assertEqual(result["status"], "needs_review")

    def test_a_type_the_model_cannot_tell_asks_the_owner(self, _):
        for recorder, event in ((Recorder(answer({"document_type": None})), "classify_document:unknown"),
                                (Recorder({"error": "x"}, status=500), "classify_document:vision_failed")):
            with self.subTest(event=event):
                result = self.run_upload(recorder, doc_type=None, **self.CONSENT)
                self.assertEqual(len(recorder.requests), 1)
                self.assertEqual(result["status"], "needs_classification")
                self.assertIn(event, result["events"])

    def test_the_photo_page_reaches_the_graph_with_no_text(self, _):
        page = read_document(UNIT / "sharp.png", "image/png")[0]
        self.assertEqual((page["text"], page["engine"]), ("", IMAGE_ENGINE))


if __name__ == "__main__":
    unittest.main()
