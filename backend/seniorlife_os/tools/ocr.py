"""Local OCR: read the text of an uploaded image or PDF (plan step 7b).

read_document(path, mime_type) returns one dict per page:
    {"page": 1, "quality": 0.97, "text": "Name: Meera Rao\\n...", "engine": "paddleocr",
     "sharpness": 812.4, "blurry": False,
     "lines": [{"text": "Name: Meera Rao", "confidence": 0.99, "box": [x0, y0, x1, y1]}, ...]}

"text" is the lines joined with newlines, so line N of "text" is lines[N - 1]. That is
the same shape the document graph already reads from its page fixtures.

PDF pages that already store text are read directly (engine "pdf_text", no OCR).
Scanned PDF pages are rendered at 300 DPI and read with PaddleOCR. All work runs on
this machine; nothing is sent anywhere.
"""

from __future__ import annotations

import statistics
import threading
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image, ImageOps

SUPPORTED_MIME_TYPES = {"application/pdf", "image/png", "image/jpeg"}
PDF_RENDER_DPI = 300
MAX_PDF_PAGES = 20
MIN_PDF_TEXT_CHARS = 20  # a PDF page with less stored text than this is treated as a scan
SHARPNESS_WIDTH = 1000  # pages are resized to this width before measuring sharpness
BLUR_THRESHOLD = 35.0  # calibrated on generated pages: blurred 12-26, all other versions 47+
BLURRY_QUALITY_CAP = 0.85  # below the 0.9 review gate, above the 0.5 re-upload cutoff

# An engine takes an RGB image array and returns (box polygon, text, confidence) items.
OcrEngine = Callable[[np.ndarray], list[tuple[list[tuple[float, float]], str, float]]]


class OcrError(RuntimeError):
    """The file could not be read. Callers should stop rather than guess."""


class OcrEngineError(OcrError):
    """The OCR engine itself failed (not the file). Worth retrying."""


class PaddleEngine:
    """PaddleOCR 3.x, English, default models (PP-OCRv6 medium det + rec).

    oneDNN (enable_mkldnn) is off because PaddlePaddle 3.3.1 crashes with it on this
    Windows CPU. Models load from the local cache (~/.paddlex) after the first download.
    """

    name = "paddleocr"

    def __init__(self) -> None:
        from paddleocr import PaddleOCR

        self.engine = PaddleOCR(
            lang="en",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            enable_mkldnn=False,
        )

    def __call__(self, image: np.ndarray) -> list[tuple[list[tuple[float, float]], str, float]]:
        # PaddleOCR reads arrays as BGR (OpenCV order).
        result = self.engine.predict(np.ascontiguousarray(image[:, :, ::-1]))[0]
        return [
            ([(float(x), float(y)) for x, y in poly], str(text), float(score))
            for poly, text, score in zip(result["rec_polys"], result["rec_texts"], result["rec_scores"])
        ]


_engine: PaddleEngine | None = None
_engine_lock = threading.Lock()


def get_paddle_engine() -> PaddleEngine:
    """Load PaddleOCR once per process (loading takes a few seconds)."""
    global _engine
    with _engine_lock:
        if _engine is None:
            _engine = PaddleEngine()
        return _engine


def read_document(path: str | Path, mime_type: str, engine: OcrEngine | None = None) -> list[dict[str, Any]]:
    """Read every page of an image or PDF. Raises OcrError if the file can't be read."""
    if mime_type not in SUPPORTED_MIME_TYPES:
        raise OcrError(f"Unsupported file type: {mime_type}")
    path = Path(path)
    if not path.is_file():
        raise OcrError("File not found")
    if mime_type == "application/pdf":
        return read_pdf(path, engine)
    return [read_image_page(load_image(path), 1, engine)]


def load_image(path: Path) -> np.ndarray:
    """Open a PNG/JPEG as an RGB array, turned upright using the phone's EXIF rotation."""
    try:
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image).convert("RGB")
            return np.asarray(image)
    except Exception as exc:  # Pillow raises several error types for broken files
        raise OcrError("Could not open the image file") from exc


def read_pdf(path: Path, engine: OcrEngine | None) -> list[dict[str, Any]]:
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
                pages.append(read_image_page(image, index + 1, engine))
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


def read_image_page(image: np.ndarray, page_number: int, engine: OcrEngine | None) -> dict[str, Any]:
    try:
        engine = engine or get_paddle_engine()
        items = engine(image)
    except Exception as exc:  # the engine failed to load or to read this page; the graph may retry
        raise OcrEngineError("The OCR engine failed on this page") from exc
    lines = group_lines(items)
    sharpness = measure_sharpness(image)
    blurry = sharpness < BLUR_THRESHOLD
    return {
        "page": page_number,
        "quality": page_quality(lines, blurry),
        "text": "\n".join(line["text"] for line in lines),
        "lines": lines,
        "engine": getattr(engine, "name", "custom"),
        "sharpness": round(sharpness, 1),
        "blurry": blurry,
    }


def group_lines(items: list[tuple[list[tuple[float, float]], str, float]]) -> list[dict[str, Any]]:
    """Join text boxes that sit on the same row, left to right, top to bottom.

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


def page_quality(lines: list[dict[str, Any]], blurry: bool) -> float:
    """Mean line confidence; a blurry page is capped so its fields go to review."""
    if not lines:
        return 0.0
    quality = statistics.fmean(line["confidence"] for line in lines)
    if blurry:
        quality = min(quality, BLURRY_QUALITY_CAP)
    return round(quality, 4)
