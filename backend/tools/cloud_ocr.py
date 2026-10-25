"""Backup OCR in the cloud over plain HTTPS (plan step 7d).

Two engines, chosen by SENIORLIFE_CLOUD_OCR (never switched automatically, so a page
only goes to the service the setting names):
- "google" (default): Google Cloud Vision, key GOOGLE_VISION_API_KEY.
- "groq": a vision language model on Groq (SENIORLIFE_VISION_MODEL, default
  qwen/qwen3.8-27b), key GROQ_API_KEY. It returns plain text only: no boxes and no
  confidence, so its lines count as unsure (0.0) and always go to review.

Used only when the senior agreed (cloud_ocr_consent; medical papers also need
medical_cloud_consent), a key is set, and the local reading left fields missing,
invalid or unsure. Before an image leaves the machine, every region that holds an
ID number, and every QR code, is painted black. Keys travel in a request header,
never in the URL, and are never logged. Swapping to an official client package later
only touches this file (Decision #17).
"""

from __future__ import annotations

import base64
import io
import os
import re
from typing import Any

import cv2
import httpx
import numpy as np
from PIL import Image, ImageDraw

from ..utils.env import find_env_file, load_env_file
from ..utils.privacy import mask_ids_in_text
from .ocr import group_lines, page_images, page_quality

VISION_URL = "https://vision.googleapis.com/v1/images:annotate"
KEY_NAME = "GOOGLE_VISION_API_KEY"
ENGINE_SETTING = "SENIORLIFE_CLOUD_OCR"
GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_KEY_NAME = "GROQ_API_KEY"
GROQ_MODEL_SETTING = "SENIORLIFE_VISION_MODEL"
GROQ_DEFAULT_MODEL = "qwen/qwen3.8-27b"
GROQ_MAX_SIDE = 2000  # longest image side sent to the model, in pixels
ENGINES = {"google": ("google_vision", "Google Vision", KEY_NAME),
           "groq": ("groq_vision", "Groq vision model", GROQ_KEY_NAME)}
TRANSCRIBE_PROMPT = (
    "Transcribe all printed text in this document image exactly as written, one printed line per "
    "output line, left to right, keeping a label and its value on the same line. Do not correct "
    "spelling, translate, explain, or add anything. Black boxes are hidden on purpose: skip them. "
    "Reply with only the transcribed text."
)
THINKING = re.compile(r"<think>.*?</think>", re.DOTALL)  # reasoning some models print first
TIMEOUT_SECONDS = 30
GROQ_TIMEOUT_SECONDS = 120
BLACKOUT_PAD = 6  # pixels added around a box before painting it black
LINE_BREAKS = {"LINE_BREAK", "EOL_SURE_SPACE"}


class CloudOcrError(RuntimeError):
    """Cloud OCR could not be used. The caller keeps the local result."""


def vision_api_key() -> str | None:
    load_env_file(find_env_file())
    return os.environ.get(KEY_NAME, "").strip() or None


def cloud_engine() -> str:
    """The engine SENIORLIFE_CLOUD_OCR names ("google" if unset). An unknown name is an error,
    never a silent switch to another service."""
    load_env_file(find_env_file())
    name = os.environ.get(ENGINE_SETTING, "").strip().lower() or "google"
    if name not in ENGINES:
        raise CloudOcrError(f"{ENGINE_SETTING} must be one of: {', '.join(sorted(ENGINES))}")
    return name


def engine_key(engine: str) -> str | None:
    load_env_file(find_env_file())
    return os.environ.get(ENGINES[engine][2], "").strip() or None


def engine_label(engine_id: str) -> str:
    """'google_vision' -> 'Google Vision' (used in review flags)."""
    return next((label for page_engine, label, _ in ENGINES.values() if page_engine == engine_id), engine_id)


def id_boxes(page: dict[str, Any], extra_boxes: list[list[int]] | None = None) -> list[list[int]]:
    """Boxes of OCR lines that show an ID number, plus boxes of ID fields found locally."""
    boxes = [line["box"] for line in page.get("lines", [])
             if line.get("box") and mask_ids_in_text(line["text"]) != line["text"]]
    return boxes + [box for box in (extra_boxes or []) if box]


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


def black_out(image: np.ndarray, boxes: list[list[int]]) -> np.ndarray:
    """A copy of the image with each box (plus a small margin) painted black."""
    picture = Image.fromarray(image)
    draw = ImageDraw.Draw(picture)
    for x0, y0, x1, y1 in boxes:
        draw.rectangle((x0 - BLACKOUT_PAD, y0 - BLACKOUT_PAD, x1 + BLACKOUT_PAD, y1 + BLACKOUT_PAD), fill=(0, 0, 0))
    return np.asarray(picture)


def _items_from_response(answer: dict[str, Any]) -> list[tuple[list[tuple[float, float]], str, float]]:
    """Vision's words joined into pieces of text that end at a line break or a paragraph end,
    so each piece is like one PaddleOCR box (a label, a value), not a single word."""
    items = []
    for page in answer.get("fullTextAnnotation", {}).get("pages", []):
        for block in page.get("blocks", []):
            for paragraph in block.get("paragraphs", []):
                words: list[dict[str, Any]] = []
                for word in paragraph.get("words", []):
                    words.append(word)
                    last = (word.get("symbols") or [{}])[-1]
                    if last.get("property", {}).get("detectedBreak", {}).get("type") in LINE_BREAKS:
                        items.append(_piece(words))
                        words = []
                if words:
                    items.append(_piece(words))
    return [item for item in items if item[1]]


def _piece(words: list[dict[str, Any]]) -> tuple[list[tuple[float, float]], str, float]:
    text, xs, ys = "", [], []
    for word in words:
        text += "".join(symbol.get("text", "") for symbol in word.get("symbols", []))
        last = (word.get("symbols") or [{}])[-1]
        if last.get("property", {}).get("detectedBreak", {}).get("type") in {"SPACE", "SURE_SPACE"}:
            text += " "
        for vertex in word.get("boundingBox", {}).get("vertices", []):
            xs.append(vertex.get("x", 0))
            ys.append(vertex.get("y", 0))
    polygon = [(min(xs), min(ys)), (max(xs), min(ys)), (max(xs), max(ys)), (min(xs), max(ys))] if xs else []
    confidence = min((word.get("confidence", 0.0) for word in words), default=0.0)
    return polygon, text.strip(), float(confidence)


def read_with_vision(image: np.ndarray, api_key: str, client: httpx.Client | None = None):
    """Send one (already blacked-out) image; return (box polygon, text, confidence) pieces."""
    buffer = io.BytesIO()
    Image.fromarray(image).save(buffer, format="PNG")
    body = {"requests": [{
        "image": {"content": base64.b64encode(buffer.getvalue()).decode("ascii")},
        "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
        "imageContext": {"languageHints": ["en"]},
    }]}
    own_client = client is None
    client = client or httpx.Client(timeout=TIMEOUT_SECONDS)
    try:
        response = client.post(VISION_URL, json=body, headers={"X-Goog-Api-Key": api_key})
    except httpx.HTTPError as exc:
        raise CloudOcrError(f"Cloud OCR could not be reached ({type(exc).__name__})") from exc
    finally:
        if own_client:
            client.close()
    if response.status_code != 200:
        raise CloudOcrError(f"Cloud OCR failed (HTTP {response.status_code})")  # body not shown: it may echo data
    try:
        answer = response.json()["responses"][0]
    except (ValueError, KeyError, IndexError) as exc:
        raise CloudOcrError("Cloud OCR sent an answer that could not be read") from exc
    if "error" in answer:
        raise CloudOcrError("Cloud OCR returned an error")
    return _items_from_response(answer)


def groq_model() -> str:
    load_env_file(find_env_file())
    return os.environ.get(GROQ_MODEL_SETTING, "").strip() or GROQ_DEFAULT_MODEL


def _shrink(image: np.ndarray, max_side: int = GROQ_MAX_SIDE) -> Image.Image:
    picture = Image.fromarray(image)
    scale = max_side / max(picture.size)
    if scale < 1:
        picture = picture.resize((round(picture.width * scale), round(picture.height * scale)), Image.LANCZOS)
    return picture


def _lines_from_text(text: str) -> list[dict[str, Any]]:
    """Model text -> OCR-style lines. No box or pieces (the model gives no positions), and
    confidence 0.0 because the model gives none: every value it gives is reviewed."""
    text = THINKING.sub("", text)
    return [{"text": " ".join(line.split()), "confidence": 0.0, "box": None}
            for line in text.splitlines() if line.strip()]


def read_with_groq_vision(image: np.ndarray, api_key: str, client: httpx.Client | None = None,
                          model: str | None = None) -> list[dict[str, Any]]:
    """Send one (already blacked-out) image to a vision model on Groq; return its lines."""
    # JPEG: a page photo is about 6x smaller than as PNG, so uploads don't time out. The black
    # boxes are painted before compression, so nothing hidden comes back.
    buffer = io.BytesIO()
    _shrink(image).convert("RGB").save(buffer, format="JPEG", quality=90)
    picture = base64.b64encode(buffer.getvalue()).decode("ascii")
    body = {
        "model": model or groq_model(),
        "temperature": 0,
        "max_completion_tokens": 4096,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": TRANSCRIBE_PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{picture}"}},
        ]}],
    }
    own_client = client is None
    client = client or httpx.Client(timeout=GROQ_TIMEOUT_SECONDS)
    try:
        response = client.post(GROQ_CHAT_URL, json=body, headers={"Authorization": f"Bearer {api_key}"})
    except httpx.HTTPError as exc:
        raise CloudOcrError(f"Cloud OCR could not be reached ({type(exc).__name__})") from exc
    finally:
        if own_client:
            client.close()
    if response.status_code != 200:
        raise CloudOcrError(f"Cloud OCR failed (HTTP {response.status_code})")  # body not shown: it may echo data
    try:
        text = response.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise CloudOcrError("Cloud OCR sent an answer that could not be read") from exc
    if not isinstance(text, str):
        raise CloudOcrError("Cloud OCR sent an answer that could not be read")
    return _lines_from_text(text)


def read_document_in_cloud(path: str, mime_type: str, local_pages: list[dict[str, Any]],
                           id_field_boxes: dict[int, list[list[int]]], api_key: str,
                           client: httpx.Client | None = None, engine: str = "google") -> list[dict[str, Any]]:
    """Read every scanned page again in the cloud, with ID regions blacked out first.

    Pages whose text was stored in a PDF (exact already) are skipped. id_field_boxes maps a
    page number to the boxes of ID fields read locally on that page.
    """
    pages = []
    for local, image in zip(local_pages, page_images(path, mime_type)):
        if local.get("engine") == "pdf_text":
            continue
        safe = black_out(image, id_boxes(local, id_field_boxes.get(local["page"], [])) + qr_boxes(image))
        if engine == "groq":
            lines = read_with_groq_vision(safe, api_key, client)
        else:
            lines = group_lines(read_with_vision(safe, api_key, client))
        pages.append({"page": local["page"], "quality": page_quality(lines, blurry=False),
                      "text": "\n".join(line["text"] for line in lines), "lines": lines,
                      "engine": ENGINES[engine][0], "sharpness": local.get("sharpness"), "blurry": False})
    return pages
