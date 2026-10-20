"""Backup OCR with Google Cloud Vision over plain HTTPS (plan step 7d).

Used only when the senior agreed (cloud_ocr_consent; medical papers also need
medical_cloud_consent), a key is set (GOOGLE_VISION_API_KEY), and the local reading
left fields missing, invalid or unsure. Before an image leaves the machine, every
region that holds an ID number is painted black. The key travels in a request
header, never in the URL, and is never logged. Swapping to the official
google-cloud-vision package later only touches this file (Decision #17).
"""

from __future__ import annotations

import base64
import io
import os
from typing import Any

import httpx
import numpy as np
from PIL import Image, ImageDraw

from ..utils.env import find_env_file, load_env_file
from ..utils.privacy import mask_ids_in_text
from .ocr import group_lines, page_images, page_quality

VISION_URL = "https://vision.googleapis.com/v1/images:annotate"
KEY_NAME = "GOOGLE_VISION_API_KEY"
TIMEOUT_SECONDS = 30
BLACKOUT_PAD = 6  # pixels added around a box before painting it black
LINE_BREAKS = {"LINE_BREAK", "EOL_SURE_SPACE"}


class CloudOcrError(RuntimeError):
    """Cloud OCR could not be used. The caller keeps the local result."""


def vision_api_key() -> str | None:
    load_env_file(find_env_file())
    return os.environ.get(KEY_NAME, "").strip() or None


def id_boxes(page: dict[str, Any], extra_boxes: list[list[int]] | None = None) -> list[list[int]]:
    """Boxes of OCR lines that show an ID number, plus boxes of ID fields found locally."""
    boxes = [line["box"] for line in page.get("lines", [])
             if line.get("box") and mask_ids_in_text(line["text"]) != line["text"]]
    return boxes + [box for box in (extra_boxes or []) if box]


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


def read_document_in_cloud(path: str, mime_type: str, local_pages: list[dict[str, Any]],
                           id_field_boxes: dict[int, list[list[int]]], api_key: str,
                           client: httpx.Client | None = None) -> list[dict[str, Any]]:
    """Read every scanned page again in the cloud, with ID regions blacked out first.

    Pages whose text was stored in a PDF (exact already) are skipped. id_field_boxes maps a
    page number to the boxes of ID fields read locally on that page.
    """
    pages = []
    for local, image in zip(local_pages, page_images(path, mime_type)):
        if local.get("engine") == "pdf_text":
            continue
        safe = black_out(image, id_boxes(local, id_field_boxes.get(local["page"], [])))
        lines = group_lines(read_with_vision(safe, api_key, client))
        pages.append({"page": local["page"], "quality": page_quality(lines, blurry=False),
                      "text": "\n".join(line["text"] for line in lines), "lines": lines,
                      "engine": "google_vision", "sharpness": local.get("sharpness"), "blurry": False})
    return pages
