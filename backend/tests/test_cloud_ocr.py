"""Tests for step 7d: what the vision model on Groq needs (tools/cloud_ocr.py): the key, busy
retries, the per-minute limit, and faces and QR codes painted black before a picture is sent.

No network: httpx's MockTransport answers instead of the service and records each request.
"""

import base64
import io
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import cv2
import httpx
import numpy as np
from PIL import Image

from backend.tools import cloud_ocr
from backend.tools.cloud_ocr import CloudOcrError, black_out, groq_api_key, qr_boxes
from backend.tools.vision_extract import ask_vision_model, safe_page_images
from backend.utils.rate_limit import CLOUD_RATE_SETTING
from test_graph_ocr import AADHAAR
from test_ocr import UNIT, ensure_fixtures

_no_env_file = patch("backend.tools.cloud_ocr.find_env_file", return_value=None)
REAL_CLOUD_LIMITER = cloud_ocr.cloud_limiter
# Fake requests need no spacing; CloudRateLimitTests below check the real limit.
_no_wait = patch("backend.tools.cloud_ocr.cloud_limiter", lambda service: MagicMock())  # acquire() never waits


def setUpModule():
    # Key and model settings are read from .env; the real one must not decide these tests
    # (and loading it would copy every key into os.environ for the tests that follow).
    _no_env_file.start()
    _no_wait.start()


def tearDownModule():
    _no_wait.stop()
    _no_env_file.stop()

FAKE_KEY = "fake-vision-key-123"
REAL_CLIENT = httpx.Client  # kept before any test swaps httpx.Client for a recorder
PROMPT = [{"type": "text", "text": "Return JSON"}]


class Recorder:
    """MockTransport handler that answers with a fixed response and keeps the requests."""

    def __init__(self, answer=None, status=200):
        self.answer, self.status, self.requests = answer, status, []

    def __call__(self, request):
        self.requests.append(request)
        return httpx.Response(self.status, json=self.answer if self.answer is not None else {})

    def client(self):
        return REAL_CLIENT(transport=httpx.MockTransport(self))

    def sent_image(self, index=0, picture=0):
        """The picture-th picture of the index-th request (a data URL in the user message)."""
        body = json.loads(self.requests[index].content)
        pictures = [part for part in body["messages"][0]["content"] if part["type"] == "image_url"]
        jpeg = base64.b64decode(pictures[picture]["image_url"]["url"].split(",", 1)[1])
        with Image.open(io.BytesIO(jpeg)) as image:
            return np.asarray(image.convert("RGB"))


class Busy(Recorder):
    """Answers "busy" (status, headers) for the first requests, then the normal answer."""

    def __init__(self, answer, busy, status=429):
        super().__init__(answer)
        self.busy, self.busy_status = list(busy), status

    def __call__(self, request):
        self.requests.append(request)
        if self.busy:
            return httpx.Response(self.busy_status, headers=self.busy.pop(0), json={"error": "busy"})
        return httpx.Response(200, json=self.answer)


def groq_answer(text):
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


@patch("backend.tools.cloud_ocr.time.sleep")
class BusyRetryTests(unittest.TestCase):
    def ask(self, recorder):
        return ask_vision_model(PROMPT, FAKE_KEY, recorder.client())

    def test_a_busy_answer_is_waited_out_and_tried_again(self, sleep):
        for status in (429, 503):
            with self.subTest(status=status):
                sleep.reset_mock()
                recorder = Busy(groq_answer('{"name": "Meera Rao"}'), [{}], status)
                self.assertEqual(self.ask(recorder), '{"name": "Meera Rao"}')
                self.assertEqual(len(recorder.requests), 2)
                self.assertEqual(recorder.requests[0].content, recorder.requests[1].content)  # same request
                sleep.assert_called_once_with(2.0)

    def test_the_wait_the_service_asks_for_is_used(self, sleep):
        recorder = Busy(groq_answer("{}"), [{"retry-after": "7"}])
        self.ask(recorder)
        sleep.assert_called_once_with(7.0)

    def test_it_gives_up_after_two_more_tries(self, sleep):
        recorder = Busy(groq_answer("{}"), [{}, {}, {}, {}])
        with self.assertRaises(CloudOcrError) as caught:
            self.ask(recorder)
        self.assertEqual(str(caught.exception), "the vision model failed (HTTP 429)")
        self.assertEqual(len(recorder.requests), 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [2.0, 4.0])

    def test_a_long_or_unreadable_wait_is_not_done(self, sleep):
        for header in ("60", "Wed, 21 Oct 2026 07:28:00 GMT"):
            with self.subTest(header=header):
                recorder = Busy(groq_answer("{}"), [{"retry-after": header}])
                with self.assertRaises(CloudOcrError):
                    self.ask(recorder)
                self.assertEqual(len(recorder.requests), 1)
        sleep.assert_not_called()

    def test_other_failures_are_not_retried(self, sleep):
        recorder = Recorder({"error": "x"}, status=500)
        with self.assertRaises(CloudOcrError):
            self.ask(recorder)
        self.assertEqual(len(recorder.requests), 1)
        sleep.assert_not_called()


class CloudRateLimitTests(unittest.TestCase):
    def test_every_attempt_waits_its_turn_under_the_limit(self):
        limiter = MagicMock()
        recorder = Busy(groq_answer("{}"), [{}])
        with patch.object(cloud_ocr, "cloud_limiter", return_value=limiter) as chosen, \
                patch("backend.tools.cloud_ocr.time.sleep"):
            ask_vision_model(PROMPT, FAKE_KEY, recorder.client())
        chosen.assert_called_once_with("groq_vision")
        self.assertEqual(limiter.acquire.call_count, 2)  # the retry is paid for too

    def test_the_real_limit_comes_from_the_setting(self):
        with patch.object(cloud_ocr, "cloud_limiter", REAL_CLOUD_LIMITER), \
                patch.dict(os.environ, {CLOUD_RATE_SETTING: "12"}):
            limiter = cloud_ocr.cloud_limiter("groq_vision")
            self.assertIs(cloud_ocr.cloud_limiter("groq_vision"), limiter)  # shared by every request
        self.assertEqual(limiter.gap, 5.0)

    def test_a_bad_setting_sends_nothing(self):
        recorder = Recorder(groq_answer("{}"))
        with patch.object(cloud_ocr, "cloud_limiter", REAL_CLOUD_LIMITER), \
                patch.dict(os.environ, {CLOUD_RATE_SETTING: "lots"}), self.assertRaises(CloudOcrError) as caught:
            ask_vision_model(PROMPT, FAKE_KEY, recorder.client())
        self.assertEqual(str(caught.exception), f"{CLOUD_RATE_SETTING} must be a number of requests per minute")
        self.assertEqual(recorder.requests, [])


class KeyTests(unittest.TestCase):
    def test_the_key_comes_from_the_setting_and_blank_means_none(self):
        for value, expected in (("  abc  ", "abc"), ("", None), ("   ", None)):
            with self.subTest(value=value), patch.dict(os.environ, {cloud_ocr.GROQ_KEY_NAME: value}):
                self.assertEqual(groq_api_key(), expected)
        with patch.dict(os.environ, {}):
            os.environ.pop(cloud_ocr.GROQ_KEY_NAME, None)
            self.assertIsNone(groq_api_key())

    def test_the_old_google_vision_and_transcription_paths_are_gone(self):
        for name in ("read_with_vision", "read_with_groq_vision", "read_document_in_cloud", "id_boxes",
                     "cloud_engine", "vision_api_key", "VISION_URL"):
            self.assertFalse(hasattr(cloud_ocr, name), name)


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
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "card.png")
            Image.fromarray(page_with_qr(tilt=True)).save(path)
            sent = safe_page_images(path, "image/png")[0]
        self.assertTrue((sent[190:290, 640:740] == 0).all())  # the middle of the code
        self.assertEqual(cv2.QRCodeDetector().detectAndDecode(sent)[0], "")
        self.assertFalse((sent[40:70, 10:400] == 0).all())  # the text line is sent


class BlackoutTests(unittest.TestCase):
    def test_box_and_margin_are_painted_black_and_nothing_else(self):
        image = np.full((100, 200, 3), 255, np.uint8)
        out = black_out(image, [[50, 40, 90, 60]])
        self.assertTrue((out[40 - cloud_ocr.BLACKOUT_PAD:60 + cloud_ocr.BLACKOUT_PAD,
                             50 - cloud_ocr.BLACKOUT_PAD:90 + cloud_ocr.BLACKOUT_PAD] == 0).all())
        self.assertTrue((out[:30] == 255).all())
        self.assertTrue((image == 255).all())  # the original is not changed


class FaceTests(unittest.TestCase):
    def test_no_face_is_found_on_a_page_of_text(self):
        ensure_fixtures()
        with Image.open(UNIT / "sharp.png") as picture:
            self.assertEqual(cloud_ocr.face_boxes(np.asarray(picture.convert("RGB"))), [])

    def test_a_found_face_gets_a_margin(self):
        detector = unittest.mock.MagicMock()
        detector.detectMultiScale.return_value = [(100, 50, 40, 40)]
        with patch.object(cloud_ocr, "_face_detector", detector):
            boxes = cloud_ocr.face_boxes(np.full((400, 600, 3), 255, np.uint8))
        self.assertEqual(boxes, [[84, 34, 156, 106]])  # 40% of the face's side on each side

    def test_faces_are_black_in_what_is_sent(self):
        ensure_fixtures()
        with patch("backend.tools.vision_extract.face_boxes", return_value=[[300, 40, 400, 140]]):
            sent = safe_page_images(str(UNIT / "sharp.png"), "image/png")[0]
        self.assertTrue((sent[40:140, 300:400] == 0).all())
        self.assertFalse((sent[0:30, 0:300] == 0).all())  # the rest is sent as it is


if __name__ == "__main__":
    unittest.main()
