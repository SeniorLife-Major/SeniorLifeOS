"""Tests for step 7b: opening an uploaded file (backend/tools/ocr.py).

A PDF that stores its text is read here; a photo or scanned page is not read on this machine
(the vision model reads it), only measured for blur. Small fixture files are written into
tests/datasets/ocr_unit/ (git-ignored; recreated when missing).
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from backend.tools import ocr
from backend.tools.demo_tools import extract_fields_from_pages

DATASETS = Path(__file__).resolve().parent / "datasets"
UNIT = DATASETS / "ocr_unit"

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


class ImagePageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_fixtures()

    def test_a_photo_is_not_read_here_only_measured(self):
        pages = ocr.read_document(UNIT / "sharp.png", "image/png")
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0], {"page": 1, "quality": 1.0, "text": "", "lines": [], "engine": ocr.IMAGE_ENGINE,
                                    "sharpness": pages[0]["sharpness"], "blurry": False})
        self.assertGreater(pages[0]["sharpness"], ocr.BLUR_THRESHOLD)

    def test_blurred_image_is_flagged_and_capped_below_the_review_gate(self):
        blurred = ocr.read_document(UNIT / "blurred.png", "image/png")[0]
        self.assertTrue(blurred["blurry"])
        self.assertLess(blurred["sharpness"], ocr.BLUR_THRESHOLD)
        self.assertEqual(blurred["quality"], ocr.BLURRY_QUALITY_CAP)
        self.assertLess(ocr.BLURRY_QUALITY_CAP, 0.9)
        self.assertGreater(ocr.BLURRY_QUALITY_CAP, 0.5)

    def test_no_local_ocr_engine_is_loaded(self):
        # PaddleOCR is gone: reading a photo must work even if the package can't be imported.
        with patch.dict(sys.modules, {"paddleocr": None, "paddle": None}):
            page = ocr.read_document(UNIT / "sharp.png", "image/png")[0]
        self.assertEqual(page["engine"], ocr.IMAGE_ENGINE)
        for name in ("PaddleEngine", "get_paddle_engine", "OcrEngineError", "read_image_page"):
            self.assertFalse(hasattr(ocr, name), name)

    def test_digital_pdf_is_read_from_its_stored_text(self):
        page = ocr.read_document(UNIT / "digital.pdf", "application/pdf")[0]
        self.assertEqual(page["engine"], "pdf_text")
        self.assertEqual(page["text"].splitlines(), DIGITAL_LINES)
        self.assertEqual(page["quality"], 1.0)

    def test_text_lines_line_up_with_line_list(self):
        page = ocr.read_document(UNIT / "digital.pdf", "application/pdf")[0]
        for number, text in enumerate(page["text"].splitlines(), start=1):
            self.assertEqual(page["lines"][number - 1]["text"], text)

    def test_output_feeds_existing_field_parser(self):
        page = ocr.read_document(UNIT / "digital.pdf", "application/pdf")[0]
        fields = extract_fields_from_pages([page])
        self.assertEqual(fields["sum_insured"]["value"], "500000")
        self.assertEqual(fields["sum_insured"]["source_ref"], "page:1:line:4")

    def test_scanned_pdf_pages_become_picture_pages(self):
        pages = ocr.read_document(UNIT / "scanned_two_pages.pdf", "application/pdf")
        self.assertEqual([page["page"] for page in pages], [1, 2])
        self.assertEqual([page["engine"] for page in pages], [ocr.IMAGE_ENGINE, ocr.IMAGE_ENGINE])
        self.assertEqual([page["text"] for page in pages], ["", ""])

    def test_scanned_pdf_pages_are_rendered_at_300_dpi_for_the_vision_model(self):
        images = ocr.page_images(UNIT / "scanned_two_pages.pdf", "application/pdf")
        # The fixture image is 1000 px wide saved at 100 DPI = 10 inches; 300 DPI gives 3000 px.
        self.assertEqual(len(images), 2)
        self.assertAlmostEqual(images[0].shape[1], 3000, delta=2)

    def test_phone_photo_is_turned_upright_from_exif(self):
        height, width = ocr.page_images(UNIT / "phone_rotated.jpg", "image/jpeg")[0].shape[:2]
        self.assertEqual((width, height), (200, 300))


class ReadDocumentFailureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_fixtures()

    def assert_fails(self, path, mime_type, message):
        with self.assertRaises(ocr.OcrError) as caught:
            ocr.read_document(path, mime_type)
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


if __name__ == "__main__":
    unittest.main()
