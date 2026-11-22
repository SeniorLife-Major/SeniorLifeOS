"""Tests: the sample lab reports (make_lab_samples.py) give the answers they say they give.

Each report's values go through the same row checks as a saved report (tools/medical.py
normalize_rows) and then the search's rules (tools/lab_specialty.py).
"""

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from backend.graphs.document_graph import MAX_UPLOAD_BYTES
from backend.tools.lab_specialty import ALL_NORMAL, read_lab_results
from backend.tools.medical import normalize_rows
from backend.tools.specialties import GENERAL_PHYSICIAN

import make_lab_samples as samples


def reading(report):
    return read_lab_results(normalize_rows("tests", samples.saved_values(report)["tests"]))


class SampleAnswerTests(unittest.TestCase):
    def test_each_report_gives_its_kind_of_doctor(self):
        for report in samples.REPORTS:
            with self.subTest(report=report["file"]):
                result = reading(report)
                self.assertEqual(result["specialty"], report["specialty"])
                self.assertEqual(result["see_today"], report["see_today"])
                self.assertEqual(result["unjudged"], 0)  # every sample row has a range we can read

    def test_the_mixed_report_names_the_kinds_of_doctor_it_saw(self):
        [mixed] = [report for report in samples.REPORTS if report["file"] == "4_mixed.png"]
        self.assertEqual(reading(mixed)["specialties"], mixed["specialties"])

    def test_the_normal_report_finds_nothing_and_says_so(self):
        [normal] = [report for report in samples.REPORTS if report["file"] == "5_all_normal.png"]
        result = reading(normal)
        self.assertEqual((result["findings"], result["specialty"], result["reason"]), ([], GENERAL_PHYSICIAN, ALL_NORMAL))

    def test_only_the_see_today_report_says_so(self):
        today = [report["file"] for report in samples.REPORTS if reading(report)["see_today"]]
        self.assertEqual(today, ["6_see_today.png"])


class SamplePagesTests(unittest.TestCase):
    def test_pages_are_drawn_with_answers(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            answers = samples.generate(out)
            self.assertEqual([item["file"] for item in answers["reports"]], [r["file"] for r in samples.REPORTS])
            for item in answers["reports"]:
                with self.subTest(file=item["file"]):
                    path = out / item["file"]
                    self.assertLess(path.stat().st_size, MAX_UPLOAD_BYTES)
                    with Image.open(path) as image:
                        self.assertEqual(image.size, (1240, 1500))
            self.assertTrue((out / "answers.json").is_file())


if __name__ == "__main__":
    unittest.main()
