"""Read an uploaded image or PDF into pages (plan step 7b).

read_document(path, mime_type) returns one dict per page. A PDF page that stores its text:
    {"page": 1, "quality": 1.0, "text": "Name: Meera Rao\n...", "engine": "pdf_text",
     "sharpness": None, "blurry": False,
     "lines": [{"text": "Name: Meera Rao", "confidence": 1.0, "box": None}, ...]}
A photo, or a scanned PDF page, is not read on this machine: its words are read by the vision
model (tools/vision_extract.py) with the senior's consent. Its page has no text, only how sharp
the picture is:
    {"page": 1, "quality": 1.0, "text": "", "lines": [], "engine": "image",
     "sharpness": 812.4, "blurry": False}
"quality" of a photo page is 1.0, or BLURRY_QUALITY_CAP when it is blurred.

"text" is the lines joined with newlines, so line N of "text" is lines[N - 1]. That is
the same shape the document graph already reads from its page fixtures.
"""

from __future__ import annotations

import statistics
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageOps

SUPPORTED_MIME_TYPES = {"application/pdf", "image/png", "image/jpeg"}
PDF_RENDER_DPI = 300
MAX_PDF_PAGES = 20
MIN_PDF_TEXT_CHARS = 20  # a PDF page with less stored text than this is treated as a scan
SHARPNESS_WIDTH = 1000  # pages are resized to this width before measuring sharpness
BLUR_THRESHOLD = 35.0  # calibrated on generated pages: blurred 12-26, all other versions 47+
BLURRY_QUALITY_CAP = 0.85  # below the 0.9 review gate, above the 0.5 re-upload cutoff
IMAGE_ENGINE = "image"  # the "engine" of a page that is a picture, read by the vision model only


class OcrError(RuntimeError):
    """The file could not be read. Callers should stop rather than guess."""


def read_document(path: str | Path, mime_type: str) -> list[dict[str, Any]]:
    """Read every page of an image or PDF. Raises OcrError if the file can't be read."""
    if mime_type not in SUPPORTED_MIME_TYPES:
        raise OcrError(f"Unsupported file type: {mime_type}")
    path = Path(path)
    if not path.is_file():
        raise OcrError("File not found")
    if mime_type == "application/pdf":
        return read_pdf(path)
    return [image_page(load_image(path), 1)]


def page_images(path: str | Path, mime_type: str) -> list[np.ndarray]:
    """The page images (upright photo, or PDF pages at 300 DPI), as they are sent to the vision model."""
    path = Path(path)
    if mime_type != "application/pdf":
        return [load_image(path)]
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(str(path))
    except Exception as exc:  # pdfium raises PdfiumError for broken or locked files
        raise OcrError("Could not open the PDF file") from exc
    try:
        return [np.asarray(pdf[index].render(scale=PDF_RENDER_DPI / 72).to_pil().convert("RGB"))
                for index in range(min(len(pdf), MAX_PDF_PAGES))]
    finally:
        pdf.close()


def load_image(path: Path) -> np.ndarray:
    """Open a PNG/JPEG as an RGB array, turned upright using the phone's EXIF rotation."""
    try:
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image).convert("RGB")
            return np.asarray(image)
    except Exception as exc:  # Pillow raises several error types for broken files
        raise OcrError("Could not open the image file") from exc


def read_pdf(path: Path) -> list[dict[str, Any]]:
    import pypdfium2 as pdfium

    try:
        pdf = pdfium.PdfDocument(str(path))
    except Exception as exc:  # pdfium raises PdfiumError for broken or locked files
        raise OcrError("Could not open the PDF file") from exc
    try:
        if len(pdf) == 0:
            raise OcrError("The PDF has no pages")
        if len(pdf) > MAX_PDF_PAGES:
            raise OcrError(f"The PDF has more than {MAX_PDF_PAGES} pages")
        pages = []
        for index in range(len(pdf)):
            page = pdf[index]
            stored = pdf_page_text(page)
            if len(stored.replace("\n", "").replace(" ", "")) >= MIN_PDF_TEXT_CHARS:
                pages.append(text_page(stored, index + 1))
            else:
                image = np.asarray(page.render(scale=PDF_RENDER_DPI / 72).to_pil().convert("RGB"))
                pages.append(image_page(image, index + 1))
        return pages
    finally:
        pdf.close()


def pdf_page_text(page: Any) -> str:
    textpage = page.get_textpage()
    try:
        return textpage.get_text_range()
    finally:
        textpage.close()


def text_page(text: str, page_number: int) -> dict[str, Any]:
    """A PDF page whose text is stored in the file: exact, so confidence is 1.0."""
    lines = [" ".join(line.split()) for line in text.splitlines()]
    lines = [{"text": line, "confidence": 1.0, "box": None} for line in lines if line]
    return {
        "page": page_number,
        "quality": 1.0,
        "text": "\n".join(line["text"] for line in lines),
        "lines": lines,
        "engine": "pdf_text",
        "sharpness": None,
        "blurry": False,
    }


def image_page(image: np.ndarray, page_number: int) -> dict[str, Any]:
    """A photo or scanned page: nothing is read here (the vision model reads it), only its
    sharpness, so a blurred photo is known."""
    sharpness = measure_sharpness(image)
    blurry = sharpness < BLUR_THRESHOLD
    return {
        "page": page_number,
        "quality": BLURRY_QUALITY_CAP if blurry else 1.0,
        "text": "",
        "lines": [],
        "engine": IMAGE_ENGINE,
        "sharpness": round(sharpness, 1),
        "blurry": blurry,
    }


def group_lines(items: list[tuple[list[tuple[float, float]], str, float]]) -> list[dict[str, Any]]:
    """Join text boxes that sit on the same row, left to right, top to bottom. No reader on this
    machine gives boxes any more; tests use this to build pages shaped like a box reading.

    Two boxes share a row when their centres are less than half a typical box
    height apart. A line's confidence is its weakest box; its box covers all of them.
    """
    boxes = []
    for poly, text, score in items:
        text = " ".join(str(text).split())
        if not text or not poly:
            continue
        xs = [point[0] for point in poly]
        ys = [point[1] for point in poly]
        boxes.append({
            "text": text, "score": float(score),
            "x0": min(xs), "y0": min(ys), "x1": max(xs), "y1": max(ys),
            "cy": (min(ys) + max(ys)) / 2,
        })
    if not boxes:
        return []
    boxes.sort(key=lambda box: box["cy"])
    row_gap = 0.5 * (statistics.median(box["y1"] - box["y0"] for box in boxes) or 1)
    rows = [[boxes[0]]]
    for box in boxes[1:]:
        if abs(box["cy"] - rows[-1][-1]["cy"]) < row_gap:
            rows[-1].append(box)
        else:
            rows.append([box])
    lines = []
    for row in rows:
        row.sort(key=lambda box: box["x0"])
        lines.append({
            "text": " ".join(box["text"] for box in row),
            "confidence": round(min(box["score"] for box in row), 4),
            "box": [
                round(min(box["x0"] for box in row)), round(min(box["y0"] for box in row)),
                round(max(box["x1"] for box in row)), round(max(box["y1"] for box in row)),
            ],
            # The separate pieces, so a two-column card can pair labels and values by position.
            "parts": [{"text": box["text"], "confidence": round(box["score"], 4),
                       "box": [round(box["x0"]), round(box["y0"]), round(box["x1"]), round(box["y1"])]}
                      for box in row],
        })
    return lines


def measure_sharpness(image: np.ndarray) -> float:
    """Variance of the Laplacian at a fixed width. Low means blurred (or very low contrast)."""
    import cv2

    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    height, width = gray.shape
    if width != SHARPNESS_WIDTH:
        size = (SHARPNESS_WIDTH, max(1, round(height * SHARPNESS_WIDTH / width)))
        gray = cv2.resize(gray, size, interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())

