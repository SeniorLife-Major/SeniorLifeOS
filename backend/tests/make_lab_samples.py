"""Sample lab reports for trying doctor search from a lab report on the website
(lab-report-doctor-search).

Six made-up reports for one made-up person, each giving a different answer. Everything is
fictional and every page says SAMPLE; the lab is a "Demo" one. The pages are drawn with the
helpers of make_hard_dataset.py, with fixed values, so each report always gives the same result.

  1_sugar.png        HbA1c, fasting and after-meal sugar high          -> Endocrinologist
  2_liver.png        SGOT, SGPT and bilirubin high                     -> Gastroenterologist
  3_kidney.png       creatinine and urea high, eGFR low                -> Nephrologist
  4_mixed.png        LDL high, TSH high, haemoglobin low               -> General physician (several)
  5_all_normal.png   every result inside its range                     -> General physician, all normal
  6_see_today.png    blood sugar 468 mg/dL                             -> Endocrinologist, see a doctor today

The kind of doctor and "see a doctor today" each report should give are checked in
test_lab_samples.py, through the same rules the search uses (tools/lab_specialty.py).

Written to tests/datasets/lab_samples/ (git-ignored), with answers.json: the values each page
prints, as they should be saved after the check step, and what the search should say.

Run from the repo root:
    backend/.venv/Scripts/python.exe backend/tests/make_lab_samples.py
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from typing import Any

from PIL import Image

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parents[1]
for path in (str(REPO), str(TESTS)):
    if path not in sys.path:
        sys.path.insert(0, path)

import make_hard_dataset as hard  # noqa: E402

OUT = TESTS / "datasets" / "lab_samples"
SEED = "lab-samples-2026-11-22"
PERSON = {"name": "Sushila Joshi", "age": 71, "sex": "F"}
LAB_NAME = "DEMO DIAGNOSTICS & PATHOLOGY LAB"

# (file, report date, what the search should say, rows: (test, result, unit, reference range)).
REPORTS: list[dict[str, Any]] = [
    {"file": "1_sugar.png", "date": "2026-11-02", "specialty": "endocrinologist", "see_today": False,
     "rows": [("HbA1c", "8.6", "%", "4.0 - 5.6"), ("Fasting Blood Sugar", "168", "mg/dL", "70 - 100"),
              ("Post Prandial Blood Sugar", "242", "mg/dL", "< 140"), ("Haemoglobin", "13.2", "g/dL", "12.0 - 15.5")]},
    {"file": "2_liver.png", "date": "2026-10-18", "specialty": "gastroenterologist", "see_today": False,
     "rows": [("SGOT (AST)", "96", "U/L", "5 - 40"), ("SGPT (ALT)", "128", "U/L", "7 - 56"),
              ("Bilirubin Total", "2.3", "mg/dL", "0.3 - 1.2"), ("Albumin", "4.1", "g/dL", "3.5 - 5.2")]},
    {"file": "3_kidney.png", "date": "2026-09-27", "specialty": "nephrologist", "see_today": False,
     "rows": [("Serum Creatinine", "2.4", "mg/dL", "0.6 - 1.2"), ("Blood Urea", "68", "mg/dL", "15 - 40"),
              ("eGFR", "31", "mL/min/1.73m2", "> 90"), ("Sodium", "138", "mmol/L", "135 - 145")]},
    {"file": "4_mixed.png", "date": "2026-11-10", "specialty": "general_physician", "see_today": False,
     "specialties": ["general_physician", "cardiologist", "endocrinologist"],
     "rows": [("LDL Cholesterol", "168", "mg/dL", "< 100"), ("TSH", "7.8", "uIU/mL", "0.4 - 4.0"),
              ("Haemoglobin", "10.4", "g/dL", "12.0 - 15.5"), ("Serum Creatinine", "0.9", "mg/dL", "0.6 - 1.2")]},
    {"file": "5_all_normal.png", "date": "2026-08-14", "specialty": "general_physician", "see_today": False,
     "findings": 0,
     "rows": [("HbA1c", "5.3", "%", "4.0 - 5.6"), ("Fasting Blood Sugar", "92", "mg/dL", "70 - 100"),
              ("Serum Creatinine", "0.9", "mg/dL", "0.6 - 1.2"), ("TSH", "2.1", "uIU/mL", "0.4 - 4.0"),
              ("Haemoglobin", "13.4", "g/dL", "12.0 - 15.5")]},
    {"file": "6_see_today.png", "date": "2026-11-20", "specialty": "endocrinologist", "see_today": True,
     "rows": [("Random Blood Sugar", "468", "mg/dL", "70 - 140"), ("HbA1c", "11.2", "%", "4.0 - 5.6"),
              ("Haemoglobin", "12.8", "g/dL", "12.0 - 15.5")]},
]


def printed_date(iso: str) -> str:
    """'2026-11-02' -> '02-Nov-2026', as the lab prints it."""
    year, month, day = iso.split("-")
    return f"{day}-{hard.MONTHS[int(month) - 1]}-{year}"


def draw(report: dict[str, Any]) -> Image.Image:
    """One lab report: the lab's header, the patient block and a ruled results table."""
    rng = random.Random(f"{SEED}:{report['file']}")
    img, page = hard.new_page(1240, 1500, (255, 255, 255))
    page.rectangle((0, 0, 1240, 130), fill=(235, 242, 250))
    page.text((60, 30), LAB_NAME, font=hard.font("arialbd.ttf", 40), fill=hard.NAVY)
    page.text((60, 85), "Demo Nagar, Mumbai  |  SAMPLE - fictional report", font=hard.font("arial.ttf", 22), fill=hard.NAVY)
    shown = printed_date(report["date"])
    info = [("Patient Name", f"Mrs. {PERSON['name']}"), ("Age / Sex", f"{PERSON['age']} Y / {PERSON['sex']}"),
            ("Ref. By", "Dr. Demo Physician"), ("Sample ID", f"LAB{rng.randint(100000, 999999)}"),
            ("Collected On", f"{shown} 08:15 AM"), ("Reported On", f"{shown} 06:40 PM")]
    label, value = hard.font("arial.ttf", 24), hard.font("arialbd.ttf", 24)
    for i, (name, text) in enumerate(info):
        x, y = 60 + (i % 2) * 600, 170 + (i // 2) * 46
        page.text((x, y), name, font=label, fill=hard.INK)
        page.text((x + 180, y), ": " + text, font=value, fill=hard.INK)
    y = 340
    columns = (60, 520, 720, 930)
    page.rectangle((50, y, 1190, y + 50), fill=(225, 230, 238))
    for x, head in zip(columns, ("Test", "Result", "Unit", "Reference Range")):
        page.text((x, y + 12), head, font=hard.font("arialbd.ttf", 26), fill=hard.INK)
    for r, (name, result, unit, ref) in enumerate(report["rows"]):
        ry = y + 70 + r * 58
        page.text((columns[0], ry), name, font=hard.font("arial.ttf", 26), fill=hard.INK)
        page.text((columns[1], ry), result, font=hard.font("arialbd.ttf", 26), fill=hard.INK)
        page.text((columns[2], ry), unit, font=hard.font("arial.ttf", 26), fill=hard.INK)
        page.text((columns[3], ry), ref, font=hard.font("arial.ttf", 26), fill=hard.INK)
        page.line((50, ry + 46, 1190, ry + 46), fill=(200, 200, 200), width=1)
    bottom = y + 70 + len(report["rows"]) * 58 + 80
    hard.signature(img, rng, 880, bottom, "Pathologist")
    page.text((860, bottom + 80), "Consultant Pathologist", font=hard.font("arial.ttf", 24), fill=hard.INK)
    page.text((60, bottom + 180), "*** End of Report ***", font=hard.font("arial.ttf", 22), fill=(90, 90, 90))
    return img


def saved_values(report: dict[str, Any]) -> dict[str, Any]:
    """The report's details as they should be saved after the check step."""
    return {"name": PERSON["name"], "lab_name": LAB_NAME, "report_date": report["date"],
            "tests": [{"name": name, "value": result, "unit": unit, "ref_range": ref}
                      for name, result, unit, ref in report["rows"]]}


def generate(out: Path = OUT) -> dict[str, Any]:
    """Draws every report into out/ and writes out/answers.json. Gives back the answers."""
    out.mkdir(parents=True, exist_ok=True)
    answers: dict[str, Any] = {"note": "Made-up lab reports, for trying doctor search from a lab report. Values are "
                                       "as they should be saved after the check step.", "reports": []}
    for report in REPORTS:
        draw(report).convert("RGB").save(out / report["file"])
        answers["reports"].append({"file": report["file"], "values": saved_values(report),
                                   "expect": {"specialty": report["specialty"], "see_today": report["see_today"]}})
    (out / "answers.json").write_text(json.dumps(answers, indent=2, ensure_ascii=False), encoding="utf-8")
    return answers


if __name__ == "__main__":
    result = generate()
    print(f"{len(result['reports'])} lab reports written to {OUT}")
