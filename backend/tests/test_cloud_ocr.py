"""Tests for step 7d: backup OCR in the cloud (tools/cloud_ocr.py + graph), Google Vision or a
vision model on Groq.

No network: httpx's MockTransport answers instead of the service and records each request.
"""

import base64
import io
import json
import os
import unittest
from unittest.mock import patch

import cv2
import httpx
import numpy as np
from PIL import Image

from backend.graphs.document_graph import build_document_graph
from backend.tools import cloud_ocr
from backend.tools.cloud_ocr import (
    GROQ_CHAT_URL, GROQ_DEFAULT_MODEL, CloudOcrError, VISION_URL, black_out, id_boxes, read_document_in_cloud,
    qr_boxes, read_with_groq_vision, read_with_vision,
)
from test_graph_ocr import AADHAAR, ocr_page, upload_state
from test_ocr import UNIT, ensure_fixtures
from backend.utils.privacy import keyed_hash
from test_privacy import FAKE_HASH_KEY, fake_aadhaar, hash_key_env

_no_env_file = patch("backend.tools.cloud_ocr.find_env_file", return_value=None)


def setUpModule():
    # Engine and model settings are read from .env; the real one must not decide these tests
    # (and loading it would copy every key into os.environ for the tests that follow).
    _no_env_file.start()


def tearDownModule():
    _no_env_file.stop()

FAKE_KEY = "fake-vision-key-123"
REAL_CLIENT = httpx.Client  # kept before any test swaps httpx.Client for a recorder


def vision_answer(lines):
    """A Vision DOCUMENT_TEXT_DETECTION answer: lines of (text, confidence, y)."""
    paragraphs = []
    for text, confidence, y in lines:
        words, x = [], 10
        tokens = text.split()
        for index, token in enumerate(tokens):
            end = "LINE_BREAK" if index == len(tokens) - 1 else "SPACE"
            symbols = [{"text": c} for c in token]
            symbols[-1]["property"] = {"detectedBreak": {"type": end}}
            width = 12 * len(token)
            words.append({"symbols": symbols, "confidence": confidence,
                          "boundingBox": {"vertices": [{"x": x, "y": y}, {"x": x + width, "y": y},
                                                       {"x": x + width, "y": y + 30}, {"x": x, "y": y + 30}]}})
            x += width + 12
        paragraphs.append({"words": words})
    return {"responses": [{"fullTextAnnotation": {"pages": [{"blocks": [{"paragraphs": paragraphs}]}]}}]}


class Recorder:
    """MockTransport handler that answers with a fixed Vision response and keeps the requests."""

    def __init__(self, answer=None, status=200):
        self.answer, self.status, self.requests = answer, status, []

    def __call__(self, request):
        self.requests.append(request)
        return httpx.Response(self.status, json=self.answer if self.answer is not None else {"responses": [{}]})

    def client(self):
        return REAL_CLIENT(transport=httpx.MockTransport(self))

    def sent_image(self, index=0):
        body = json.loads(self.requests[index].content)
        if "messages" in body:  # Groq: the picture is a data URL in the user message
            url = body["messages"][0]["content"][1]["image_url"]["url"]
            png = base64.b64decode(url.split(",", 1)[1])
        else:
            png = base64.b64decode(body["requests"][0]["image"]["content"])
        with Image.open(io.BytesIO(png)) as image:
            return np.asarray(image.convert("RGB"))


class VisionRequestTests(unittest.TestCase):
    def test_key_goes_in_a_header_never_in_the_url(self):
        recorder = Recorder(vision_answer([("Name: Meera Rao", 0.98, 10)]))
        read_with_vision(np.full((60, 400, 3), 255, np.uint8), FAKE_KEY, recorder.client())
        request = recorder.requests[0]
        self.assertEqual(str(request.url), VISION_URL)
        self.assertNotIn(FAKE_KEY, str(request.url))
        self.assertEqual(request.headers["X-Goog-Api-Key"], FAKE_KEY)
        body = json.loads(request.content)["requests"][0]
        self.assertEqual(body["features"], [{"type": "DOCUMENT_TEXT_DETECTION"}])

    def test_words_become_one_piece_per_line(self):
        recorder = Recorder(vision_answer([("Member Name", 0.97, 10), ("Meera Rao", 0.95, 50)]))
        items = read_with_vision(np.zeros((90, 400, 3), np.uint8), FAKE_KEY, recorder.client())
        self.assertEqual([(text, conf) for _, text, conf in items], [("Member Name", 0.97), ("Meera Rao", 0.95)])

    def test_failures_never_show_the_answer_body(self):
        for recorder in (Recorder({"error": {"message": f"bad key {FAKE_KEY}"}}, status=403),
                         Recorder({"responses": [{"error": {"message": "quota"}}]})):
            with self.subTest(status=recorder.status), self.assertRaises(CloudOcrError) as caught:
                read_with_vision(np.zeros((10, 10, 3), np.uint8), FAKE_KEY, recorder.client())
            self.assertNotIn(FAKE_KEY, str(caught.exception))

    def test_network_error_becomes_cloud_ocr_error(self):
        def refuse(request):
            raise httpx.ConnectError("no route")
        client = httpx.Client(transport=httpx.MockTransport(refuse))
        with self.assertRaises(CloudOcrError):
            read_with_vision(np.zeros((10, 10, 3), np.uint8), FAKE_KEY, client)


def groq_answer(text):
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


class GroqVisionRequestTests(unittest.TestCase):
    def test_key_goes_in_a_header_and_the_picture_in_the_message(self):
        recorder = Recorder(groq_answer("Name: Meera Rao"))
        read_with_groq_vision(np.full((60, 400, 3), 255, np.uint8), FAKE_KEY, recorder.client())
        request = recorder.requests[0]
        self.assertEqual(str(request.url), GROQ_CHAT_URL)
        self.assertEqual(request.headers["Authorization"], f"Bearer {FAKE_KEY}")
        self.assertNotIn(FAKE_KEY, str(request.url) + request.content.decode())
        body = json.loads(request.content)
        self.assertEqual(body["model"], GROQ_DEFAULT_MODEL)
        self.assertEqual(body["temperature"], 0)
        self.assertTrue(body["messages"][0]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))

    def test_text_becomes_unsure_lines_without_boxes(self):
        recorder = Recorder(groq_answer("<think>looking</think>\nName:  Meera Rao\n\n  Issue date: 23/12/2025 \n"))
        lines = read_with_groq_vision(np.zeros((10, 10, 3), np.uint8), FAKE_KEY, recorder.client())
        self.assertEqual(lines, [{"text": "Name: Meera Rao", "confidence": 0.0, "box": None},
                                 {"text": "Issue date: 23/12/2025", "confidence": 0.0, "box": None}])

    def test_large_pages_are_shrunk_before_sending(self):
        recorder = Recorder(groq_answer("x"))
        read_with_groq_vision(np.full((3000, 1500, 3), 255, np.uint8), FAKE_KEY, recorder.client())
        self.assertEqual(recorder.sent_image().shape[:2], (2000, 1000))

    def test_failures_never_show_the_answer_body(self):
        for recorder in (Recorder({"error": {"message": f"bad key {FAKE_KEY}"}}, status=401),
                         Recorder({"choices": []}), Recorder(groq_answer(None))):
            with self.subTest(status=recorder.status), self.assertRaises(CloudOcrError) as caught:
                read_with_groq_vision(np.zeros((10, 10, 3), np.uint8), FAKE_KEY, recorder.client())
            self.assertNotIn(FAKE_KEY, str(caught.exception))


def page_with_qr(tilt=False):
    """A white page (900x500) with a text-like bar and a real QR code holding a fake Aadhaar number."""
    qr = cv2.QRCodeEncoder.create().encode(f"UID {AADHAAR.replace(' ', '')} SAMPLE")
    qr = np.pad(cv2.resize(qr, (qr.shape[1] * 6, qr.shape[0] * 6), interpolation=cv2.INTER_NEAREST), 24,
                constant_values=255)
    page = np.full((500, 900, 3), 255, np.uint8)
    page[40:70, 10:400] = 90  # stands in for a text line
    h, w = qr.shape
    corners = [[600, 150], [600 + w, 150], [600 + w, 150 + h], [600, 150 + h]]
    if tilt:
        corners = [[600, 150], [600 + w, 160], [595 + w, 150 + h], [605, 145 + h]]
    matrix = cv2.getPerspectiveTransform(np.float32([[0, 0], [w, 0], [w, h], [0, h]]), np.float32(corners))
    warped = cv2.warpPerspective(cv2.cvtColor(qr, cv2.COLOR_GRAY2RGB), matrix, (900, 500), borderValue=(0, 0, 0))
    inside = cv2.warpPerspective(np.full((h, w), 255, np.uint8), matrix, (900, 500)) > 0
    page[inside] = warped[inside]
    return page


class QrTests(unittest.TestCase):
    def test_qr_codes_are_found_flat_and_tilted(self):
        for tilt in (False, True):
            with self.subTest(tilt=tilt):
                boxes = qr_boxes(page_with_qr(tilt))
                self.assertTrue(boxes)
                x0, y0, x1, y1 = boxes[0]
                self.assertTrue(x0 <= 630 and y0 <= 180 and x1 >= 600 + 140 and y1 >= 150 + 140)

    def test_a_page_without_a_qr_code_has_none(self):
        self.assertEqual(qr_boxes(np.full((300, 300, 3), 255, np.uint8)), [])

    def test_the_qr_code_is_black_in_what_is_sent_and_cannot_be_read(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "card.png")
            Image.fromarray(page_with_qr(tilt=True)).save(path)
            local = {"page": 1, "text": "Name: Meera Rao",
                     "lines": [{"text": "Name: Meera Rao", "confidence": 0.6, "box": [10, 40, 400, 70]}]}
            for engine, answer in (("google", vision_answer([])), ("groq", groq_answer("Name: Meera Rao"))):
                with self.subTest(engine=engine):
                    recorder = Recorder(answer)
                    read_document_in_cloud(path, "image/png", [local], {}, FAKE_KEY, recorder.client(), engine=engine)
                    sent = recorder.sent_image()
                    self.assertTrue((sent[190:290, 640:740] < 40).all())  # the middle of the code
                    self.assertEqual(cv2.QRCodeDetector().detectAndDecode(sent)[0], "")
                    self.assertFalse((sent[40:70, 10:400] < 40).all())  # the text line is sent


class BlackoutTests(unittest.TestCase):
    def test_box_and_margin_are_painted_black_and_nothing_else(self):
        image = np.full((100, 200, 3), 255, np.uint8)
        out = black_out(image, [[50, 40, 90, 60]])
        self.assertTrue((out[40 - cloud_ocr.BLACKOUT_PAD:60 + cloud_ocr.BLACKOUT_PAD,
                             50 - cloud_ocr.BLACKOUT_PAD:90 + cloud_ocr.BLACKOUT_PAD] == 0).all())
        self.assertTrue((out[:30] == 255).all())
        self.assertTrue((image == 255).all())  # the original is not changed

    def test_lines_showing_an_id_number_are_found(self):
        page = ocr_page([("Name: Meera Rao", 0.99), (f"Card {AADHAAR}", 0.99), ("PAN HAVPC1814K", 0.99)])
        self.assertEqual(id_boxes(page, [[1, 2, 3, 4]]), [[10, 40, 600, 70], [10, 80, 600, 110], [1, 2, 3, 4]])


class ReadDocumentInCloudTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_fixtures()

    def test_id_regions_are_black_in_what_is_sent(self):
        page = ocr_page([("Name: Meera Rao", 0.6), ("Annual income: 1,20,000", 0.99), (f"Aadhaar {AADHAAR}", 0.99)])
        recorder = Recorder(vision_answer([("Name: Meera Rao", 0.98, 0)]))
        pages = read_document_in_cloud(str(UNIT / "sharp.png"), "image/png", [page], {}, FAKE_KEY, recorder.client())
        sent = recorder.sent_image()
        self.assertTrue((sent[80:110, 10:600] == 0).all())      # the Aadhaar line
        self.assertFalse((sent[0:30, 10:600] == 0).all())       # the name line is sent as it is
        self.assertEqual(pages[0]["engine"], "google_vision")
        self.assertEqual(pages[0]["text"], "Name: Meera Rao")

    def test_id_regions_are_black_for_the_groq_engine_too(self):
        page = ocr_page([("Name: Meera Rao", 0.6), ("Annual income: 1,20,000", 0.99), (f"Aadhaar {AADHAAR}", 0.99)])
        recorder = Recorder(groq_answer("Name: Meera Rao"))
        pages = read_document_in_cloud(str(UNIT / "sharp.png"), "image/png", [page], {}, FAKE_KEY, recorder.client(),
                                       engine="groq")
        sent = recorder.sent_image()
        self.assertLessEqual(max(sent.shape[:2]), cloud_ocr.GROQ_MAX_SIDE)
        self.assertTrue((sent[80:110, 10:600] < 40).all())  # JPEG: black, give or take compression noise
        self.assertFalse((sent[0:30, 10:600] < 40).all())
        self.assertEqual((pages[0]["engine"], pages[0]["text"]), ("groq_vision", "Name: Meera Rao"))

    def test_pdf_pages_with_stored_text_are_not_sent(self):
        local = [{"page": 1, "engine": "pdf_text", "text": "Name: Meera Rao", "lines": []}]
        recorder = Recorder(vision_answer([]))
        pages = read_document_in_cloud(str(UNIT / "digital.pdf"), "application/pdf", local, {}, FAKE_KEY,
                                       recorder.client())
        self.assertEqual((pages, recorder.requests), ([], []))


@patch("backend.utils.privacy.find_env_file", return_value=None)
class GraphCloudOcrTests(unittest.TestCase):
    LOCAL = [("Name: Meera Ra0", 0.62), ("Annual income: 1,20,000", 0.99), ("Issue date: 2026-06-01", 0.99),
             (f"Aadhaar: {AADHAAR}", 0.99)]
    CLOUD = [("Name: Meera Rao", 0.98, 0), ("Annual income: 1,20,000", 0.98, 40), ("Issue date: 2026-06-01", 0.98, 80)]

    @classmethod
    def setUpClass(cls):
        ensure_fixtures()

    def run_upload(self, recorder, key=FAKE_KEY, doc_type="income_certificate", engine=None, key_name=None,
                   **extra):
        state = upload_state(UNIT / "sharp.png", "image/png", doc_type, **extra)
        key_name = key_name or cloud_ocr.KEY_NAME
        with patch("backend.graphs.document_graph.read_document", return_value=[ocr_page(self.LOCAL)]), \
                patch("backend.tools.cloud_ocr.httpx.Client", lambda **_: recorder.client()), \
                patch("backend.tools.cloud_ocr.find_env_file", return_value=None), \
                patch.dict(os.environ, {}), hash_key_env(FAKE_HASH_KEY):
            for name in (cloud_ocr.KEY_NAME, cloud_ocr.GROQ_KEY_NAME, cloud_ocr.ENGINE_SETTING):
                os.environ.pop(name, None)  # never the developer's own settings
            if key:
                os.environ[key_name] = key
            if engine:
                os.environ[cloud_ocr.ENGINE_SETTING] = engine
            return build_document_graph().invoke(state)

    def test_no_consent_no_cloud(self, _):
        recorder = Recorder(vision_answer(self.CLOUD))
        result = self.run_upload(recorder)
        self.assertEqual(recorder.requests, [])
        self.assertFalse(any(event.startswith("cloud_ocr") for event in result["events"]))

    def test_unsure_field_is_read_again_and_reviewed(self, _):
        recorder = Recorder(vision_answer(self.CLOUD))
        result = self.run_upload(recorder, cloud_ocr_consent=True)
        self.assertEqual(len(recorder.requests), 1)
        self.assertIn("cloud_ocr:replaced_1", result["events"])
        self.assertEqual(result["events"].count("validate_candidates"), 2)
        name = result["extracted_fields"]["name"]
        self.assertEqual((name["value"], name["source"], name["source_ref"]),
                         ("Meera Rao", "cloud_ocr", "cloud:page:1:line:1"))
        self.assertIn("name: read again by cloud OCR (Google Vision), please check", result["review_flags"])
        self.assertEqual(result["status"], "needs_review")

    def test_id_numbers_stay_local_and_everything_is_masked(self, _):
        recorder = Recorder(vision_answer(self.CLOUD))
        result = self.run_upload(recorder, cloud_ocr_consent=True,
                                 review_decision={"action": "confirm", "reviewer_id": "senior-meera"})
        self.assertEqual(result["status"], "confirmed")
        self.assertTrue((recorder.sent_image()[120:150, 10:600] == 0).all())  # the Aadhaar line was blacked out
        self.assertEqual(result["vault_record"]["confirmed_fields"]["aadhaar_number"]["value"], "XXXX XXXX 7973")
        self.assertNotIn(AADHAAR, json.dumps(result, default=str))
        self.assertIn("cloud_pages", result["document"])

    def test_missing_key_or_cloud_failure_keeps_the_local_result(self, _):
        for recorder, key, event in ((Recorder(vision_answer(self.CLOUD)), None, "cloud_ocr:no_key"),
                                     (Recorder({"error": "x"}, status=500), FAKE_KEY, "cloud_ocr:failed")):
            with self.subTest(event=event):
                result = self.run_upload(recorder, key=key, cloud_ocr_consent=True)
                self.assertIn(event, result["events"])
                self.assertEqual(result["extracted_fields"]["name"]["value"], "Meera Ra0")
                self.assertTrue(any(w.startswith("Cloud OCR") for w in result["warnings"]))
                self.assertNotIn(FAKE_KEY, json.dumps(result, default=str))

    def test_medical_pages_also_need_medical_consent(self, _):
        recorder = Recorder(vision_answer(self.CLOUD))
        self.run_upload(recorder, doc_type="lab_report", cloud_ocr_consent=True)
        self.assertEqual(recorder.requests, [])

    def test_an_id_number_from_the_cloud_never_replaces_the_local_one(self, _):
        other = fake_aadhaar(31)  # a different, checksum-valid number
        self.LOCAL = [*self.LOCAL[:3], (f"Aadhaar: {AADHAAR}", 0.7)]  # the local reading is unsure of it
        recorder = Recorder(vision_answer([*self.CLOUD, (f"Aadhaar: {other}", 0.99, 120)]))
        result = self.run_upload(recorder, cloud_ocr_consent=True,
                                 review_decision={"action": "confirm", "reviewer_id": "senior-meera"})
        self.assertEqual(result["vault_record"]["confirmed_fields"]["aadhaar_number"]["value"], "XXXX XXXX 7973")
        self.assertEqual(result["vault_record"]["identifier_hashes"]["aadhaar_number"],
                         keyed_hash("aadhaar_number", AADHAAR.replace(" ", ""), FAKE_HASH_KEY))

    def test_an_id_number_only_the_cloud_read_is_masked_too(self, _):
        recorder = Recorder(vision_answer([*self.CLOUD, ("Card HAVPC1814K", 0.99, 160)]))
        result = self.run_upload(recorder, cloud_ocr_consent=True,
                                 review_decision={"action": "confirm", "reviewer_id": "senior-meera"})
        self.assertEqual(result["status"], "confirmed")
        self.assertIn("Card XXXXXX814K", result["document"]["cloud_pages"][0]["text"])
        self.assertNotIn("HAVPC1814K", json.dumps(result, default=str))

    def test_id_card_whose_number_was_not_found_is_never_sent(self, _):
        # Local OCR read the card but not the Aadhaar number, so its place on the page is unknown
        # and it could not be blacked out: the page must stay on the machine.
        self.LOCAL = [("Name: Meera Ra0", 0.62), ("DOB: 01/01/1950", 0.99), ("2377 72B3 5757", 0.4)]
        recorder = Recorder(vision_answer(self.CLOUD))
        result = self.run_upload(recorder, doc_type="identity", cloud_ocr_consent=True)
        self.assertEqual(recorder.requests, [])
        self.assertIn("cloud_ocr:id_not_located", result["events"])
        self.assertIn("Cloud OCR skipped: the ID number on this document could not be found on the page to "
                      "black it out", result["warnings"])
        self.assertEqual(result["extracted_fields"]["name"]["value"], "Meera Ra0")  # local result kept
        self.assertEqual(result["status"], "needs_review")

    def test_id_card_whose_number_was_found_is_sent_with_it_blacked_out(self, _):
        self.LOCAL = [("Name: Meera Ra0", 0.62), ("DOB: 01/01/1950", 0.99), (f"Aadhaar: {AADHAAR}", 0.99)]
        recorder = Recorder(vision_answer(self.CLOUD))
        result = self.run_upload(recorder, doc_type="identity", cloud_ocr_consent=True)
        self.assertEqual(len(recorder.requests), 1)
        self.assertNotIn("cloud_ocr:id_not_located", result["events"])
        self.assertTrue((recorder.sent_image()[80:110, 10:600] == 0).all())  # the Aadhaar line

    def test_cloud_is_asked_only_once(self, _):
        recorder = Recorder(vision_answer([("Name: Meera Ra0", 0.5, 0)]))  # the cloud is unsure too
        result = self.run_upload(recorder, cloud_ocr_consent=True)
        self.assertEqual(len(recorder.requests), 1)
        self.assertEqual(sum(event.startswith("cloud_ocr") for event in result["events"]), 1)

    def test_groq_engine_reads_again_and_names_itself_in_the_flag(self, _):
        recorder = Recorder(groq_answer("Name: Meera Rao\nAnnual income: 1,20,000\nIssue date: 2026-06-01"))
        result = self.run_upload(recorder, engine="groq", key_name=cloud_ocr.GROQ_KEY_NAME, cloud_ocr_consent=True)
        self.assertEqual(str(recorder.requests[0].url), GROQ_CHAT_URL)
        self.assertIn("cloud_ocr:replaced_1", result["events"])
        self.assertEqual(result["extracted_fields"]["name"]["cloud_engine"], "groq_vision")
        self.assertIn("name: read again by cloud OCR (Groq vision model), please check", result["review_flags"])
        self.assertIn("name: low confidence (0.00)", result["review_flags"])  # the model gives no confidence
        self.assertEqual(result["status"], "needs_review")

    def test_groq_engine_without_a_groq_key_never_falls_back_to_google(self, _):
        recorder = Recorder(groq_answer("Name: Meera Rao"))
        result = self.run_upload(recorder, engine="groq", key_name=cloud_ocr.KEY_NAME, cloud_ocr_consent=True)
        self.assertEqual(recorder.requests, [])
        self.assertIn("cloud_ocr:no_key", result["events"])
        self.assertIn("Cloud OCR skipped: GROQ_API_KEY is not set", result["warnings"])

    def test_a_cut_off_issuer_is_read_again_and_replaced(self, _):
        self.LOCAL = [("Name: Meera Rao", 0.99), ("Issuing authority: Medical Board, Demo D", 0.98),
                      ("Issue date: 10/11/2025", 0.99), ("Disability: 40%", 0.99)]
        cloud = [("Name: Meera Rao", 0.98, 0), ("Issuing authority: Medical Board, Demo District Hospital", 0.97, 40)]
        recorder = Recorder(vision_answer(cloud))
        result = self.run_upload(recorder, doc_type="disability_certificate", cloud_ocr_consent=True)
        self.assertEqual(len(recorder.requests), 1)  # asked only because the issuer looked cut off
        self.assertEqual(result["extracted_fields"]["issuing_authority"]["value"],
                         "Medical Board, Demo District Hospital")
        self.assertIn("issuing_authority: read again by cloud OCR (Google Vision), please check",
                      result["review_flags"])
        self.assertNotIn("issuing_authority: may be cut off, please check", result["review_flags"])

    def test_a_cloud_value_that_also_looks_wrong_does_not_replace(self, _):
        self.LOCAL = [("Name: Meera Rao", 0.99), ("Issuing authority: Medical Board, Demo D", 0.98),
                      ("Issue date: 10/11/2025", 0.99), ("Disability: 40%", 0.99)]
        recorder = Recorder(vision_answer([("Issuing authority: Medical Board, Demo Distriet", 0.97, 40)]))
        result = self.run_upload(recorder, doc_type="disability_certificate", cloud_ocr_consent=True)
        self.assertEqual(result["extracted_fields"]["issuing_authority"]["value"], "Medical Board, Demo D")
        self.assertIn("issuing_authority: may be cut off, please check", result["review_flags"])

    def test_a_different_cloud_reading_of_a_sure_name_is_flagged(self, _):
        self.LOCAL = [("Name: Meera Ra0", 0.6), ("Issuing authority: Medical Board, Demo District Hospital", 0.99),
                      ("Issue date: 10/11/2025", 0.99), ("Disability: 40%", 0.99)]
        cloud = [("Name: Meera Rao", 0.98, 0), ("Issuing authority: Medical Board, Demo Civil Hospital", 0.97, 40)]
        result = self.run_upload(Recorder(vision_answer(cloud)), doc_type="disability_certificate",
                                 cloud_ocr_consent=True)
        self.assertEqual(result["extracted_fields"]["issuing_authority"]["value"],
                         "Medical Board, Demo District Hospital")  # the sure local reading is kept
        self.assertEqual(result["cloud_disagreements"], ["issuing_authority"])
        self.assertIn("issuing_authority: the cloud reading differs, please check", result["review_flags"])

    def test_the_same_cloud_reading_raises_nothing(self, _):
        self.LOCAL = [("Name: Meera Ra0", 0.6), ("Issuing authority: Medical Board, Demo District Hospital", 0.99),
                      ("Issue date: 10/11/2025", 0.99), ("Disability: 40%", 0.99)]
        cloud = [("Name: Meera Rao", 0.98, 0), ("Issuing authority: MEDICAL BOARD, DEMO DISTRICT HOSPITAL.", 0.97, 40)]
        result = self.run_upload(Recorder(vision_answer(cloud)), doc_type="disability_certificate",
                                 cloud_ocr_consent=True)
        self.assertEqual(result["cloud_disagreements"], [])
        self.assertFalse(any("cloud reading differs" in flag for flag in result["review_flags"]))

    def test_unknown_engine_name_sends_nothing(self, _):
        recorder = Recorder(groq_answer("Name: Meera Rao"))
        result = self.run_upload(recorder, engine="azure", cloud_ocr_consent=True)
        self.assertEqual(recorder.requests, [])
        self.assertIn("cloud_ocr:bad_setting", result["events"])
        self.assertEqual(result["extracted_fields"]["name"]["value"], "Meera Ra0")


if __name__ == "__main__":
    unittest.main()
