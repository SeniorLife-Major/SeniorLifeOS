"""Tests for step 7b: local OCR (backend/tools/ocr.py).

Fast tests use a fake OCR engine and small fixture files written into
tests/datasets/ocr_unit/ (git-ignored; recreated when missing).

Slow tests run the real PaddleOCR on the benchmark pages in
tests/datasets/ocr_benchmark/. They take a few minutes, so they only run when
SENIORLIFE_RUN_OCR_TESTS=1 is set.
"""

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from backend.tools import ocr
from backend.tools.demo_tools import FIELD_LABELS, extract_fields_from_pages, normalize_field

DATASETS = Path(__file__).resolve().parent / "datasets"
UNIT = DATASETS / "ocr_unit"
BENCHMARK = DATASETS / "ocr_benchmark"
RUN_SLOW = os.environ.get("SENIORLIFE_RUN_OCR_TESTS") == "1"

DIGITAL_LINES = [
    "Name: Meera Rao",
    "Insurer: Demo Health Insurance Ltd",
    "Policy No: HLT-2026/004512",
    "Sum insured: 500000",
    "Valid till: 2027-03-31",
]


# ---------- fixture files ----------

def write_digital_pdf(path: Path, lines: list[str]) -> None:
    """A one-page PDF whose text is stored in the file (no image)."""
    content = "BT /F1 14 Tf 72 760 Td 18 TL " + " ".join(f"({line}) Tj T*" for line in lines) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    pdf = "%PDF-1.4\n"
    offsets = []
    for number, obj in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf += f"{number} 0 obj\n{obj}\nendobj\n"
    xref = len(pdf)
    pdf += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n" + "".join(f"{o:010d} 00000 n \n" for o in offsets)
    pdf += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    path.write_bytes(pdf.encode("latin-1"))


def text_image(lines: list[str]) -> Image.Image:
    image = Image.new("RGB", (1000, 120 + 60 * len(lines)), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=36)
    for index, line in enumerate(lines):
        draw.text((60, 60 + 60 * index), line, fill="black", font=font)
    return image


def blank_pdf(path: Path, page_count: int) -> None:
    pdf = pdfium.PdfDocument.new()
    for _ in range(page_count):
        pdf.new_page(200, 200)
    pdf.save(str(path))
    pdf.close()


def ensure_fixtures() -> None:
    UNIT.mkdir(parents=True, exist_ok=True)
    sharp = text_image(["Name: Meera Rao", "Annual income: 120000", "Issue date: 2026-06-01"])
    makers = {
        "digital.pdf": lambda p: write_digital_pdf(p, DIGITAL_LINES),
        "sharp.png": lambda p: sharp.save(p),
        "blurred.png": lambda p: sharp.filter(ImageFilter.GaussianBlur(4)).save(p),
        "scanned_two_pages.pdf": lambda p: sharp.save(p, "PDF", resolution=100, save_all=True, append_images=[sharp]),
        "too_many_pages.pdf": lambda p: blank_pdf(p, ocr.MAX_PDF_PAGES + 1),
        "no_pages.pdf": lambda p: blank_pdf(p, 0),
        "broken.png": lambda p: p.write_bytes(b"not really an image"),
        "broken.pdf": lambda p: p.write_bytes(b"%PDF-1.4 broken"),
    }
    for name, make in makers.items():
        if not (UNIT / name).exists():
            make(UNIT / name)
    rotated = UNIT / "phone_rotated.jpg"
    if not rotated.exists():
        # Stored sideways (300 wide x 200 high) with EXIF "rotate 90 degrees" (orientation 6).
        exif = Image.Exif()
        exif[0x0112] = 6
        Image.new("RGB", (300, 200), "white").save(rotated, exif=exif.tobytes())


class FakeEngine:
    """Stands in for PaddleOCR: returns fixed boxes and records what it was given."""

    name = "fake"

    def __init__(self, items=None, fail=False):
        self.items = items if items is not None else [
            ([(60, 60), (400, 60), (400, 96), (60, 96)], "Name: Meera Rao", 0.99),
            ([(60, 120), (300, 120), (300, 156), (60, 156)], "Annual income:", 0.97),
            ([(320, 122), (480, 122), (480, 158), (320, 158)], "120000", 0.91),
        ]
        self.fail = fail
        self.images = []

    def __call__(self, image):
        self.images.append(image)
        if self.fail:
            raise RuntimeError("engine crashed")
        return self.items


def box(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


# ---------- fast tests ----------

class GroupLinesTests(unittest.TestCase):
    def test_boxes_on_one_row_join_left_to_right(self):
        lines = ocr.group_lines([
            (box(320, 100, 480, 136), "1,20,000", 0.91),
            (box(60, 102, 300, 138), "Annual income:", 0.97),
            (box(60, 40, 300, 76), "Name: Meera Rao", 0.99),
        ])
        self.assertEqual([line["text"] for line in lines], ["Name: Meera Rao", "Annual income: 1,20,000"])

    def test_line_keeps_weakest_confidence_and_covering_box(self):
        line = ocr.group_lines([(box(60, 102, 300, 138), "Annual income:", 0.97),
                                (box(320, 100, 480, 136), "1,20,000", 0.91)])[0]
        self.assertEqual(line["confidence"], 0.91)
        self.assertEqual(line["box"], [60, 100, 480, 138])

    def test_empty_boxes_and_extra_spaces_are_dropped(self):
        lines = ocr.group_lines([(box(0, 0, 10, 10), "   ", 0.5), (box(0, 50, 200, 80), " Issuer:   Demo  Office ", 0.9)])
        self.assertEqual([line["text"] for line in lines], ["Issuer: Demo Office"])
        self.assertEqual(ocr.group_lines([]), [])


class PageQualityTests(unittest.TestCase):
    def test_quality_is_mean_line_confidence(self):
        lines = [{"confidence": 1.0}, {"confidence": 0.8}]
        self.assertEqual(ocr.page_quality(lines, blurry=False), 0.9)

    def test_blurry_page_is_capped_below_review_gate(self):
        self.assertEqual(ocr.page_quality([{"confidence": 0.99}], blurry=True), ocr.BLURRY_QUALITY_CAP)
        self.assertLess(ocr.BLURRY_QUALITY_CAP, 0.9)
        self.assertGreater(ocr.BLURRY_QUALITY_CAP, 0.5)

    def test_page_with_no_text_has_zero_quality(self):
        self.assertEqual(ocr.page_quality([], blurry=False), 0.0)

    def test_blurred_image_is_flagged_and_sharp_one_is_not(self):
        ensure_fixtures()
        sharp = ocr.read_document(UNIT / "sharp.png", "image/png", FakeEngine())[0]
        blurred = ocr.read_document(UNIT / "blurred.png", "image/png", FakeEngine())[0]
        self.assertFalse(sharp["blurry"])
        self.assertTrue(blurred["blurry"])
        self.assertLess(blurred["sharpness"], ocr.BLUR_THRESHOLD)
        self.assertEqual(blurred["quality"], ocr.BLURRY_QUALITY_CAP)


class ReadDocumentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_fixtures()

    def test_image_page_shape(self):
        engine = FakeEngine()
        pages = ocr.read_document(UNIT / "sharp.png", "image/png", engine)
        self.assertEqual(len(pages), 1)
        page = pages[0]
        self.assertEqual(page["page"], 1)
        self.assertEqual(page["engine"], "fake")
        self.assertEqual(page["text"], "Name: Meera Rao\nAnnual income: 120000")
        self.assertEqual(page["quality"], round((0.99 + 0.91) / 2, 4))
        self.assertEqual(engine.images[0].shape[2], 3)

    def test_text_lines_line_up_with_line_list(self):
        page = ocr.read_document(UNIT / "sharp.png", "image/png", FakeEngine())[0]
        for number, text in enumerate(page["text"].splitlines(), start=1):
            self.assertEqual(page["lines"][number - 1]["text"], text)

    def test_output_feeds_existing_field_parser(self):
        page = ocr.read_document(UNIT / "sharp.png", "image/png", FakeEngine())[0]
        fields = extract_fields_from_pages([page])
        self.assertEqual(fields["annual_income"]["value"], "120000")
        self.assertEqual(fields["annual_income"]["source_ref"], "page:1:line:2")

    def test_digital_pdf_is_read_without_ocr(self):
        engine = FakeEngine()
        page = ocr.read_document(UNIT / "digital.pdf", "application/pdf", engine)[0]
        self.assertEqual(engine.images, [])
        self.assertEqual(page["engine"], "pdf_text")
        self.assertEqual(page["text"].splitlines(), DIGITAL_LINES)
        self.assertEqual(page["quality"], 1.0)

    def test_scanned_pdf_pages_are_rendered_at_300_dpi_and_ocrd(self):
        engine = FakeEngine()
        pages = ocr.read_document(UNIT / "scanned_two_pages.pdf", "application/pdf", engine)
        self.assertEqual([page["page"] for page in pages], [1, 2])
        self.assertEqual([page["engine"] for page in pages], ["fake", "fake"])
        # The fixture image is 1000 px wide saved at 100 DPI = 10 inches; 300 DPI gives 3000 px.
        self.assertEqual(len(engine.images), 2)
        self.assertAlmostEqual(engine.images[0].shape[1], 3000, delta=2)

    def test_phone_photo_is_turned_upright_from_exif(self):
        engine = FakeEngine()
        ocr.read_document(UNIT / "phone_rotated.jpg", "image/jpeg", engine)
        height, width = engine.images[0].shape[:2]
        self.assertEqual((width, height), (200, 300))

    def test_paddle_wrapper_sends_bgr_and_reads_results(self):
        captured = {}

        class Predictor:
            def predict(self, array):
                captured["array"] = array
                return [{"rec_polys": [np.array([[1, 2], [3, 2], [3, 4], [1, 4]])],
                         "rec_texts": ["Name: Meera Rao"], "rec_scores": [np.float32(0.98)]}]

        wrapper = object.__new__(ocr.PaddleEngine)
        wrapper.engine = Predictor()
        rgb = np.zeros((2, 2, 3), dtype=np.uint8)
        rgb[..., 0] = 255  # pure red
        items = wrapper(rgb)
        self.assertEqual(captured["array"][0, 0].tolist(), [0, 0, 255])  # red in BGR order
        self.assertEqual(items[0][1], "Name: Meera Rao")
        self.assertAlmostEqual(items[0][2], 0.98, places=5)
        self.assertEqual(items[0][0][0], (1.0, 2.0))


class ReadDocumentFailureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_fixtures()

    def assert_fails(self, path, mime_type, message, engine=None):
        with self.assertRaises(ocr.OcrError) as caught:
            ocr.read_document(path, mime_type, engine or FakeEngine())
        self.assertIn(message, str(caught.exception))

    def test_unsupported_type(self):
        self.assert_fails(UNIT / "sharp.png", "image/gif", "Unsupported file type")

    def test_missing_file(self):
        self.assert_fails(UNIT / "does_not_exist.png", "image/png", "File not found")

    def test_broken_image(self):
        self.assert_fails(UNIT / "broken.png", "image/png", "Could not open the image file")

    def test_broken_pdf(self):
        self.assert_fails(UNIT / "broken.pdf", "application/pdf", "Could not open the PDF file")

    def test_image_sent_as_pdf_is_rejected(self):
        self.assert_fails(UNIT / "sharp.png", "application/pdf", "Could not open the PDF file")

    def test_pdf_with_no_pages(self):
        # pdfium refuses to open a saved 0-page PDF, so this stops at opening.
        self.assert_fails(UNIT / "no_pages.pdf", "application/pdf", "Could not open the PDF file")

    def test_opened_pdf_with_no_pages(self):
        class EmptyPdf:
            closed = False

            def __len__(self):
                return 0

            def close(self):
                EmptyPdf.closed = True

        with patch("pypdfium2.PdfDocument", return_value=EmptyPdf()):
            self.assert_fails(UNIT / "digital.pdf", "application/pdf", "The PDF has no pages")
        self.assertTrue(EmptyPdf.closed)

    def test_pdf_with_too_many_pages(self):
        self.assert_fails(UNIT / "too_many_pages.pdf", "application/pdf", f"more than {ocr.MAX_PDF_PAGES} pages")

    def test_engine_crash_becomes_ocr_error(self):
        self.assert_fails(UNIT / "sharp.png", "image/png", "OCR engine failed", FakeEngine(fail=True))


# ---------- slow tests: real PaddleOCR ----------

@unittest.skipUnless(RUN_SLOW, "set SENIORLIFE_RUN_OCR_TESTS=1 to run real PaddleOCR (a few minutes)")
class PaddleOcrTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        manifest_path = BENCHMARK / "manifest.json"
        if not manifest_path.exists():
            raise unittest.SkipTest(f"benchmark dataset missing: {manifest_path}")
        cls.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    def page_entry(self, doc_type, variant):
        return next(p for p in self.manifest if p["type"] == doc_type and p["variant"] == variant)

    def test_clean_pages_give_every_field_exactly(self):
        for entry in (p for p in self.manifest if p["variant"] == "clean"):
            with self.subTest(document=entry["type"]):
                page = ocr.read_document(BENCHMARK / entry["path"], "image/png")[0]
                self.assertEqual(page["engine"], "paddleocr")
                self.assertFalse(page["blurry"])
                self.assertGreaterEqual(page["quality"], 0.9)
                fields = extract_fields_from_pages([page])
                for label, value in entry["fields"]:
                    key = FIELD_LABELS.get(label.lower())
                    if key is None:
                        continue
                    self.assertIn(key, fields, label)
                    self.assertEqual(normalize_field(key, fields[key]["value"]), normalize_field(key, value), label)

    def test_blurred_page_is_flagged_for_review(self):
        entry = self.page_entry("income_certificate", "blur")
        page = ocr.read_document(BENCHMARK / entry["path"], "image/png")[0]
        self.assertTrue(page["blurry"])
        self.assertLessEqual(page["quality"], ocr.BLURRY_QUALITY_CAP)

    def test_scanned_pdf_is_ocrd(self):
        page = ocr.read_document(BENCHMARK / "pages" / "scanned.pdf", "application/pdf")[0]
        self.assertEqual(page["engine"], "paddleocr")
        self.assertIn("10,00,000", page["text"])
        self.assertIn("Name: Lakshmi Narayanan", page["text"].splitlines())

    def test_digital_pdf_from_benchmark(self):
        page = ocr.read_document(BENCHMARK / "pages" / "digital.pdf", "application/pdf")[0]
        self.assertEqual(page["engine"], "pdf_text")
        self.assertEqual(page["text"].splitlines(), DIGITAL_LINES)


if __name__ == "__main__":
    unittest.main()
