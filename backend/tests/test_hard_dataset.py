"""Tests for the harder fake-document generator (tests/make_hard_dataset.py).

These check the generator itself (no OCR): the answers stored in the manifest are
really printed on the page, the fake numbers are valid, output is repeatable, every
damage effect changes the picture, and the scoring is right. Files go to
tests/datasets/ocr_hard_unit/ (git-ignored).
"""

import hashlib
import random
import shutil
import unittest
from datetime import datetime
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw

import make_hard_dataset as hard
from seniorlife_os.tools.demo_tools import normalize_field

UNIT = hard.OUT_DIR.parent / "ocr_hard_unit"


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_rgb(path):
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"))


class DrawnText:
    """Records every string drawn while a page is made."""

    def __init__(self):
        self.strings = []
        self.original = ImageDraw.ImageDraw.text

    def __enter__(self):
        recorder = self

        def spy(draw, xy, text, *args, **kwargs):
            recorder.strings.append(text)
            return recorder.original(draw, xy, text, *args, **kwargs)

        self.patcher = patch.object(ImageDraw.ImageDraw, "text", spy)
        self.patcher.start()
        return self

    def __exit__(self, *exc):
        self.patcher.stop()


class LayoutTests(unittest.TestCase):
    def make(self, doc_type, seed=7):
        person = hard.PEOPLE[hard.TYPES.index(doc_type) % len(hard.PEOPLE)]
        with DrawnText() as drawn:
            img, fields, rows = hard.MAKERS[doc_type](random.Random(seed), person)
        return img, fields, rows, " | ".join(drawn.strings)

    def test_every_stored_answer_is_printed_on_the_page(self):
        for doc_type in hard.TYPES:
            for seed in range(5):
                _, fields, rows, printed = self.make(doc_type, seed)
                with self.subTest(document=doc_type, seed=seed):
                    for key, truth in fields.items():
                        self.assertIn(truth["shown"], printed, key)
                    for row in rows:
                        for cell in row:
                            self.assertIn(cell, printed)

    def test_every_page_says_sample(self):
        for doc_type in hard.TYPES:
            with self.subTest(document=doc_type):
                self.assertIn("SAMPLE", self.make(doc_type)[3])

    def test_fields_needed_by_each_type_are_present(self):
        from seniorlife_os.tools.document_rules import REQUIRED_FIELDS

        for doc_type in REQUIRED_FIELDS:
            with self.subTest(document=doc_type):
                self.assertTrue(REQUIRED_FIELDS[doc_type] <= set(self.make(doc_type)[1]))

    def test_fake_numbers_are_valid_and_answers_match_what_is_printed(self):
        for seed in range(20):
            fields = {}
            for doc_type in hard.TYPES:
                fields.update({f"{doc_type}.{k}": v for k, v in self.make(doc_type, seed)[1].items()})
            with self.subTest(seed=seed):
                aadhaar = fields["identity.aadhaar_number"]
                self.assertEqual(normalize_field("aadhaar_number", aadhaar["shown"]), aadhaar["value"])
                self.assertEqual(normalize_field("ifsc", fields["bank_passbook.ifsc"]["shown"]),
                                 fields["bank_passbook.ifsc"]["value"])
                self.assertIsNotNone(normalize_field("account_number", fields["bank_passbook.account_number"]["shown"]))
                self.assertIsNotNone(normalize_field("policy_number", fields["health_insurance.policy_number"]["shown"]))
                for key, fmt in (("identity.date_of_birth", "%d/%m/%Y"), ("income_certificate.issue_date", "%d/%m/%Y"),
                                 ("residence_proof.issue_date", "%d-%m-%Y"), ("health_insurance.expiry_date", "%d-%b-%Y"),
                                 ("disability_certificate.issue_date", "%d.%m.%Y")):
                    self.assertEqual(datetime.strptime(fields[key]["shown"], fmt).date().isoformat(), fields[key]["value"], key)
                for key in ("income_certificate.annual_income", "health_insurance.sum_insured"):
                    self.assertEqual(int(fields[key]["shown"].replace(",", "")), fields[key]["value"], key)
                percent = fields["disability_certificate.disability_percent"]
                self.assertEqual(percent["shown"], f"{percent['value']}%")
                self.assertEqual(hard.STATES[fields["residence_proof.residence_state"]["shown"]],
                                 fields["residence_proof.residence_state"]["value"])

    def test_indian_number_formats(self):
        self.assertEqual(hard.inr(266000), "2,66,000")
        self.assertEqual(hard.inr(10000000), "1,00,00,000")
        self.assertEqual(hard.inr(950), "950")
        self.assertEqual(hard.rupees_in_words(266000), "Two Lakh Sixty Six Thousand")
        self.assertEqual(hard.rupees_in_words(1_20_05_019), "One Crore Twenty Lakh Five Thousand Nineteen")


class GenerateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        shutil.rmtree(UNIT, ignore_errors=True)
        cls.manifest = hard.generate(UNIT / "a", types=["identity"], conditions=hard.CONDITIONS)
        hard.generate(UNIT / "b", types=["identity"], conditions=hard.CONDITIONS)

    def test_same_seed_gives_identical_files(self):
        for entry in self.manifest:
            self.assertEqual(file_hash(UNIT / "a" / entry["path"]), file_hash(UNIT / "b" / entry["path"]), entry["path"])
        self.assertEqual((UNIT / "a" / "manifest.json").read_text(encoding="utf-8"),
                         (UNIT / "b" / "manifest.json").read_text(encoding="utf-8"))

    def test_one_page_per_condition_with_relative_paths(self):
        self.assertEqual([e["condition"] for e in self.manifest], hard.CONDITIONS)
        for entry in self.manifest:
            self.assertTrue(entry["path"].startswith("pages/"))
            self.assertTrue((UNIT / "a" / entry["path"]).is_file())

    def test_every_condition_changes_the_picture_differently(self):
        base = load_rgb(UNIT / "a" / "pages" / "identity__base.png")
        pictures = {}
        for entry in self.manifest:
            arr = load_rgb(UNIT / "a" / entry["path"])
            pictures[entry["condition"]] = arr
            if arr.shape == base.shape:
                self.assertGreater(np.abs(arr.astype(int) - base).mean(), 2, entry["condition"])
        hashes = {c: hashlib.sha256(a.tobytes()).hexdigest() for c, a in pictures.items()}
        self.assertEqual(len(set(hashes.values())), len(hard.CONDITIONS))

    def test_phone_conditions_put_the_paper_on_a_table(self):
        base = load_rgb(UNIT / "a" / "pages" / "identity__base.png")
        photo = load_rgb(UNIT / "a" / "pages" / "identity__phone_photo.png")
        self.assertGreater(photo.shape[1], base.shape[1])
        corner = photo[:20, :20].mean()
        self.assertLess(corner, 200)  # table colour, not white paper

    def test_unknown_condition_is_rejected(self):
        with self.assertRaises(ValueError):
            hard.apply_condition(Image.new("RGB", (10, 10)), "underwater", random.Random(1))


class ScorePageTests(unittest.TestCase):
    entry = {
        "fields": {
            "name": {"shown": "Meera Rao", "value": "Meera Rao"},
            "issue_date": {"shown": "17/12/2026", "value": "2026-12-17"},
            "annual_income": {"shown": "2,66,000", "value": 266000},
        },
        "rows": [["Haemoglobin", "11.2", "g/dL", "12.0 - 15.5"]],
    }

    def test_read_ignores_case_and_spaces_but_parsed_needs_the_right_value(self):
        page = {"page": 1, "quality": 0.9, "text": "Name: MEERA  RAO\nDate: 17/12/2026\nAnnual income: Rs. 2,66,000/-\n"
                                                  "Haemoglobin 11.2 g/dL 12.0 - 15.5"}
        result = hard.score_page(self.entry, page)
        self.assertEqual(result["read"], {"name": True, "issue_date": True, "annual_income": True})
        # Name is read but keeps OCR's capitals (not the stored value); "Date" alone is not a
        # known label. "Rs. 2,66,000/-" is understood since step 7c-3.
        self.assertEqual(result["parsed"], {"name": False, "issue_date": False, "annual_income": True})
        self.assertEqual(result["rows_ok"], 1)

    def test_parser_friendly_text_counts_as_parsed(self):
        page = {"page": 1, "quality": 1.0, "text": "Name: Meera Rao\nIssue date: 2026-12-17\nAnnual income: 2,66,000"}
        result = hard.score_page(self.entry, page)
        self.assertEqual(result["parsed"], {"name": True, "issue_date": True, "annual_income": True})
        self.assertEqual(result["rows_ok"], 0)

    def test_misread_value_is_not_counted(self):
        page = {"page": 1, "quality": 1.0, "text": "Name: Meera Rao\nAnnual income: 22,66,000"}
        result = hard.score_page(self.entry, page)
        self.assertFalse(result["read"]["issue_date"])
        self.assertFalse(result["read"]["annual_income"])  # 2,66,000 hides inside 22,66,000
        self.assertFalse(result["parsed"]["annual_income"])

    def test_two_values_on_one_row_are_both_read(self):
        # Insurance-card rows hold two values; neither may be rejected for sitting next to the other.
        entry = {"fields": {"start": {"shown": "01-Aug-2028", "value": None},
                            "end": {"shown": "28-Aug-2029", "value": None},
                            "sum": {"shown": "5,00,000", "value": None}}, "rows": []}
        page = {"page": 1, "quality": 1.0, "text": "01-Aug-2028 28-Aug-2029\nMID33221319 5,00,000"}
        self.assertEqual(hard.score_page(entry, page)["read"], {"start": True, "end": True, "sum": True})

    def test_spaces_inside_a_value_may_vanish_or_appear(self):
        entry = {"fields": {"aadhaar": {"shown": "5439 0214 9647", "value": None},
                            "pan": {"shown": "HAVPC1814K", "value": None}}, "rows": []}
        page = {"page": 1, "quality": 1.0, "text": "543902149647\nPAN: HAVPC 1814K"}
        self.assertEqual(hard.score_page(entry, page)["read"], {"aadhaar": True, "pan": True})

    def test_lab_row_spacing_is_tolerated_but_a_shifted_row_is_not(self):
        entry = {"fields": {}, "rows": [["Haemoglobin", "15.2", "g/dL", "12.0 - 15.5"],
                                        ["TSH", "5.5", "uIU/mL", "0.4 - 4.0"]]}
        page = {"page": 1, "quality": 1.0,
                "text": "Haemoglobin 15.2 g/dL 12.0-15.5\nTSH Total Cholesterol 252.4 mg/dL\nX 5.5 uIU/mL 0.4 - 4.0"}
        self.assertEqual(hard.score_page(entry, page)["rows_ok"], 1)

    def test_value_split_across_lines_is_not_counted(self):
        page = {"page": 1, "quality": 1.0, "text": "Name: Meera\nRao"}
        self.assertFalse(hard.score_page(self.entry, page)["read"]["name"])


if __name__ == "__main__":
    unittest.main()
