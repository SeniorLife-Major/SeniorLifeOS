"""Tests for step 7d: Google Vision backup OCR (tools/cloud_ocr.py + graph).

No network: httpx's MockTransport answers instead of Google and records each request.
"""

import base64
import io
import json
import os
import unittest
from unittest.mock import patch

import httpx
import numpy as np
from PIL import Image

from backend.graphs.document_graph import build_document_graph
from backend.tools import cloud_ocr
from backend.tools.cloud_ocr import (
    CloudOcrError, VISION_URL, black_out, id_boxes, read_document_in_cloud, read_with_vision,
)
from test_graph_ocr import AADHAAR, ocr_page, upload_state
from test_ocr import UNIT, ensure_fixtures
from backend.utils.privacy import keyed_hash
from test_privacy import FAKE_HASH_KEY, fake_aadhaar, hash_key_env

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

    def run_upload(self, recorder, key=FAKE_KEY, doc_type="income_certificate", **extra):
        state = upload_state(UNIT / "sharp.png", "image/png", doc_type, **extra)
        env = {cloud_ocr.KEY_NAME: key} if key else {}
        with patch("backend.graphs.document_graph.read_document", return_value=[ocr_page(self.LOCAL)]), \
                patch("backend.tools.cloud_ocr.httpx.Client", lambda **_: recorder.client()), \
                patch("backend.tools.cloud_ocr.find_env_file", return_value=None), \
                patch.dict(os.environ, env), hash_key_env(FAKE_HASH_KEY):
            if not key:
                os.environ.pop(cloud_ocr.KEY_NAME, None)
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

    def test_cloud_is_asked_only_once(self, _):
        recorder = Recorder(vision_answer([("Name: Meera Ra0", 0.5, 0)]))  # the cloud is unsure too
        result = self.run_upload(recorder, cloud_ocr_consent=True)
        self.assertEqual(len(recorder.requests), 1)
        self.assertEqual(sum(event.startswith("cloud_ocr") for event in result["events"]), 1)


if __name__ == "__main__":
    unittest.main()
