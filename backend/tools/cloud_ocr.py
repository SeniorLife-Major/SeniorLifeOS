"""What the vision model on Groq needs (plan step 7d): the key, the model, the busy retries and
the per-minute limit, and painting faces and QR codes black before a picture leaves the machine.

The model itself is asked in tools/vision_extract.py. A page goes there only when the senior
agreed (cloud_ocr_consent; medical papers also need medical_cloud_consent) and GROQ_API_KEY is
set. Keys travel in a request header, never in the URL, and are never logged. Swapping to an
official client package later only touches this file and vision_extract (Decision #17).
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

import cv2
import httpx
import numpy as np
from PIL import Image, ImageDraw

from ..utils.env import find_env_file, load_env_file
from ..utils.rate_limit import CLOUD_RATE_SETTING, DEFAULT_CLOUD_PER_MINUTE, SpacedRateLimiter, rate_limiter

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_KEY_NAME = "GROQ_API_KEY"
GROQ_MODEL_SETTING = "SENIORLIFE_VISION_MODEL"
GROQ_DEFAULT_MODEL = "qwen/qwen3.8-27b"
GROQ_MAX_SIDE = 2000  # longest image side sent to the model, in pixels
THINKING = re.compile(r"<think>.*?</think>", re.DOTALL)  # reasoning some models print first
GROQ_TIMEOUT_SECONDS = 120
BLACKOUT_PAD = 6  # pixels added around a box before painting it black
BUSY_STATUSES = {429, 503}  # the service is busy or rate limited: worth one more try after a wait
MAX_RETRIES = 2
MAX_WAIT_SECONDS = 20  # a longer wait asked for by the service is not done: the owner is waiting


class CloudOcrError(RuntimeError):
    """The vision model could not be used. The caller goes on without it."""


def groq_api_key() -> str | None:
    load_env_file(find_env_file())
    return os.environ.get(GROQ_KEY_NAME, "").strip() or None


def qr_boxes(image: np.ndarray) -> list[list[int]]:
    """Boxes of QR codes in the image, with a margin. An ID card's QR holds personal data
    (older Aadhaar QR codes even hold the full number), so it is never sent. Found with
    OpenCV's two QR detectors; a code too damaged to be detected is not covered."""
    boxes = []
    for detector in (cv2.QRCodeDetector(), cv2.QRCodeDetectorAruco()):
        try:
            found, points = detector.detectMulti(image)
        except cv2.error:
            continue
        if not found or points is None:
            continue
        for quad in points:
            x0, y0 = quad.min(axis=0)
            x1, y1 = quad.max(axis=0)
            margin = 0.1 * max(x1 - x0, y1 - y0)
            boxes.append([int(x0 - margin), int(y0 - margin), int(x1 + margin) + 1, int(y1 + margin) + 1])
    return boxes


FACE_MODEL = "haarcascade_frontalface_default.xml"  # ships with OpenCV
_face_detector: cv2.CascadeClassifier | None = None


def face_boxes(image: np.ndarray) -> list[list[int]]:
    """Boxes of faces (an ID card photo), with a margin: no model needs a face to read a document.
    OpenCV's frontal-face detector; a face it misses (turned away, very small) is not covered."""
    global _face_detector
    if _face_detector is None:
        _face_detector = cv2.CascadeClassifier(str(Path(cv2.data.haarcascades) / FACE_MODEL))
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY) if image.ndim == 3 else image
    side = max(24, min(gray.shape[:2]) // 25)
    boxes = []
    for x, y, w, h in _face_detector.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(side, side)):
        margin = int(0.4 * max(w, h))  # hair, chin and ears too
        boxes.append([int(x) - margin, int(y) - margin, int(x + w) + margin, int(y + h) + margin])
    return boxes


def black_out(image: np.ndarray, boxes: list[list[int]]) -> np.ndarray:
    """A copy of the image with each box (plus a small margin) painted black."""
    picture = Image.fromarray(image)
    draw = ImageDraw.Draw(picture)
    for x0, y0, x1, y1 in boxes:
        draw.rectangle((x0 - BLACKOUT_PAD, y0 - BLACKOUT_PAD, x1 + BLACKOUT_PAD, y1 + BLACKOUT_PAD), fill=(0, 0, 0))
    return np.asarray(picture)


def busy_wait(response: httpx.Response, attempt: int) -> float | None:
    """Seconds to wait before trying again: the service's Retry-After, else 2 then 4 seconds.
    None when the service asks for longer than MAX_WAIT_SECONDS (or for a date): give up now."""
    asked = response.headers.get("retry-after")
    if asked is None:
        return float(2 ** (attempt + 1))
    try:
        seconds = float(asked)
    except ValueError:
        return None
    return max(seconds, 0.0) if seconds <= MAX_WAIT_SECONDS else None


def cloud_limiter(service: str) -> SpacedRateLimiter:
    """The shared requests-per-minute limit of one paid cloud service (SENIORLIFE_CLOUD_MAX_PER_MINUTE)."""
    try:
        return rate_limiter(service, CLOUD_RATE_SETTING, DEFAULT_CLOUD_PER_MINUTE)
    except RuntimeError as exc:  # a bad setting: nothing is sent
        raise CloudOcrError(str(exc)) from None


def post_with_retry(client: httpx.Client, url: str, service: str, **kwargs: Any) -> httpx.Response:
    """POST, and when the service answers busy (429 or 503), wait and send the same request
    again, at most MAX_RETRIES times. Any other answer is returned at once. Every attempt,
    a retry too, first waits its turn under the service's per-minute limit (credits are paid)."""
    limiter = cloud_limiter(service)
    for attempt in range(MAX_RETRIES + 1):
        limiter.acquire()
        response = client.post(url, **kwargs)
        if response.status_code not in BUSY_STATUSES or attempt == MAX_RETRIES:
            return response
        wait = busy_wait(response, attempt)
        if wait is None:
            return response
        time.sleep(wait)
    return response


def groq_model() -> str:
    load_env_file(find_env_file())
    return os.environ.get(GROQ_MODEL_SETTING, "").strip() or GROQ_DEFAULT_MODEL


def _shrink(image: np.ndarray, max_side: int = GROQ_MAX_SIDE) -> Image.Image:
    picture = Image.fromarray(image)
    scale = max_side / max(picture.size)
    if scale < 1:
        picture = picture.resize((round(picture.width * scale), round(picture.height * scale)), Image.LANCZOS)
    return picture
