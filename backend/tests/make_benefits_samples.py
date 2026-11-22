"""Sample papers for trying the benefits check on the website (benefits Part 2).

Four made-up people, each with the papers one account would hold, so "Schemes for me" can be
seen giving every kind of answer. Everything is fictional and every page says SAMPLE; the
offices are "Demo" ones. The pages are drawn with the helpers of make_hard_dataset.py, but with
fixed values instead of random ones, so each person always gets the same result.

  1_kamla_devi         widow, 68, PHH card (her son is the head): widow and old age pensions
  2_ramesh_gupta       man, 75, AAY card, 85% disability: health cover, old age and disability
  3_anita_deshpande    woman, 66, APL card: none (not a poor household, not 70 yet)
  4_lakshmi_narayanan  widow, 72, PHH card; her name on the death certificate is one letter
                       off, so the widow pension asks her to check it

Kamla Devi's values are those of test_benefits_schemes.py. The results each person should get
are checked in test_benefits_samples.py.

Written to tests/datasets/benefits_samples/ (git-ignored), with answers.json: the values each
page prints, as they should be saved after the check step.

Run from the repo root:
    backend/.venv/Scripts/python.exe backend/tests/make_benefits_samples.py
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
from backend.utils.privacy import verhoeff_check_digit  # noqa: E402

OUT = TESTS / "datasets" / "benefits_samples"
SEED = "benefits-samples-2026-11-22"
STATE_NAMES = {"MH": "Maharashtra", "UP": "Uttar Pradesh", "KA": "Karnataka", "TN": "Tamil Nadu"}
CARD_WORDS = {"AAY": "Antyodaya (AAY)", "PHH": "Priority Household (PHH)", "APL": "APL"}
GENDER_WORDS = {"female": ("महिला", "FEMALE"), "male": ("पुरुष", "MALE")}
BANK = "Demo Co-operative Bank Ltd."
REGISTRAR = "Registrar, Demo Municipal Council"
MEDICAL_BOARD = "Medical Board, Demo District Hospital"


def aadhaar(body: str) -> str:
    """A made-up 12-digit number that passes the Aadhaar check digit (the reader checks it)."""
    return body + verhoeff_check_digit(body)


def paper(file: str, document_type: str, values: dict[str, Any], **extra: Any) -> dict[str, Any]:
    """values: what the vault should hold after the check step (dates as YYYY-MM-DD, codes)."""
    return {"file": f"{file}.png", "type": document_type, "values": values, **extra}


PEOPLE: list[dict[str, Any]] = [
    {
        "key": "kamla", "folder": "1_kamla_devi", "name": "Kamla Devi", "hindi": "कमला देवी", "title": "Smt.",
        "papers": [
            paper("1_id_card", "identity", {"name": "Kamla Devi", "date_of_birth": "1958-03-12", "gender": "female",
                                            "aadhaar_number": aadhaar("73429158063")}),
            paper("2_ration_card", "ration_card", {
                "head_of_family": "Raju Prasad", "card_category": "PHH", "residence_state": "MH",
                "family_members": "Raju Prasad, Kamla Devi, Sita Prasad", "ration_card_number": "MH2704581236"}),
            paper("3_bank_passbook", "bank_passbook", {"name": "Kamla Devi", "bank_name": BANK,
                                                       "account_number": "30418825716", "ifsc": "DEMO0004321"}),
            paper("4_death_certificate", "death_certificate", {
                "deceased_name": "Ramesh Prasad", "date_of_death": "2024-02-14", "age_at_death": 71,
                "place_of_death": "Demo Taluka", "spouse_name": "Kamla Devi",
                "registration_number": "D-2024:27-01452", "issue_date": "2024-03-02",
                "issuing_authority": REGISTRAR}),
            # Someone else's passbook: added to her papers, every scheme says "Please check".
            paper("extra_passbook_other_name", "bank_passbook", {
                "name": "Sunita Sharma", "bank_name": BANK, "account_number": "30418890244",
                "ifsc": "DEMO0004321"}, extra=True),
        ],
    },
    {
        "key": "ramesh", "folder": "2_ramesh_gupta", "name": "Ramesh Chandra Gupta", "hindi": "रमेश चंद्र गुप्ता",
        "title": "Shri",
        "papers": [
            paper("1_id_card", "identity", {"name": "Ramesh Chandra Gupta", "date_of_birth": "1951-08-05",
                                            "gender": "male", "aadhaar_number": aadhaar("58216497301")}),
            paper("2_ration_card", "ration_card", {
                "head_of_family": "Ramesh Chandra Gupta", "card_category": "AAY", "residence_state": "UP",
                "family_members": "Ramesh Chandra Gupta, Savitri Gupta", "ration_card_number": "UP0931175820"}),
            paper("3_bank_passbook", "bank_passbook", {"name": "Ramesh Chandra Gupta", "bank_name": BANK,
                                                       "account_number": "51200763348", "ifsc": "DEMO0007788"}),
            paper("4_disability_certificate", "disability_certificate", {
                "name": "Ramesh Chandra Gupta", "issuing_authority": MEDICAL_BOARD, "issue_date": "2025-01-10",
                "disability_percent": 85}, age_then=73),
        ],
    },
    {
        "key": "anita", "folder": "3_anita_deshpande", "name": "Anita Deshpande", "hindi": "अनीता देशपांडे",
        "title": "Smt.",
        "papers": [
            paper("1_id_card", "identity", {"name": "Anita Deshpande", "date_of_birth": "1960-06-21", "gender": "female",
                                            "aadhaar_number": aadhaar("46670318825")}),
            paper("2_ration_card", "ration_card", {
                "head_of_family": "Anita Deshpande", "card_category": "APL", "residence_state": "KA",
                "family_members": "Anita Deshpande, Rohan Deshpande", "ration_card_number": "KA1588204617"}),
            paper("3_bank_passbook", "bank_passbook", {"name": "Anita Deshpande", "bank_name": BANK,
                                                       "account_number": "77104529930", "ifsc": "DEMO0002215"}),
        ],
    },
    {
        "key": "lakshmi", "folder": "4_lakshmi_narayanan", "name": "Lakshmi Narayanan", "hindi": "लक्ष्मी नारायणन",
        "title": "Smt.",
        "papers": [
            paper("1_id_card", "identity", {"name": "Lakshmi Narayanan", "date_of_birth": "1954-11-03",
                                            "gender": "female", "aadhaar_number": aadhaar("39051862274")}),
            paper("2_ration_card", "ration_card", {
                "head_of_family": "Lakshmi Narayanan", "card_category": "PHH", "residence_state": "TN",
                "family_members": "Lakshmi Narayanan, Karthik Narayanan", "ration_card_number": "TN3307716452"}),
            paper("3_bank_passbook", "bank_passbook", {"name": "Lakshmi Narayanan", "bank_name": BANK,
                                                       "account_number": "62018847715", "ifsc": "DEMO0005530"}),
            # Her name one letter short ("Narayana"): close, but not the same, so she is asked to check.
            paper("4_death_certificate", "death_certificate", {
                "deceased_name": "Mohan Narayanan", "date_of_death": "2023-07-09", "age_at_death": 76,
                "place_of_death": "Demo Nagar", "spouse_name": "Lakshmi Narayana",
                "registration_number": "D-2023:33-04418", "issue_date": "2023-08-21",
                "issuing_authority": REGISTRAR}),
        ],
    },
]


def person(key: str) -> dict[str, Any]:
    return next(item for item in PEOPLE if item["key"] == key)


def printed_date(value: str, separator: str) -> str:
    """'1958-03-12' -> '12/03/1958' (day first, as Indian papers print it)."""
    year, month, day = value.split("-")
    return separator.join((day, month, year))


# ---------- pages (layouts of make_hard_dataset.py, with fixed values) ----------

def draw_identity(who: dict[str, Any], v: dict[str, Any], rng: random.Random) -> Image.Image:
    img, draw = hard.new_page(1012, 638, (247, 244, 236))
    hard.guilloche(img, rng, (200, 120, 60))
    draw.rectangle((0, 0, 1012, 90), fill=(236, 120, 40))
    draw.text((40, 12), "नमूना पहचान पत्र", font=hard.font("Nirmala.ttc", 30), fill=(255, 255, 255))
    draw.text((40, 50), "SAMPLE IDENTITY CARD", font=hard.font("arialbd.ttf", 28), fill=(255, 255, 255))
    hard.photo_box(img, (40, 130, 230, 360))
    hindi = hard.font("Nirmala.ttc", 30)
    gender_hi, gender_en = GENDER_WORDS[v["gender"]]
    draw.text((270, 135), who["hindi"], font=hindi, fill=hard.INK)
    draw.text((270, 185), v["name"], font=hard.font("arial.ttf", 32), fill=hard.INK)
    draw.text((270, 240), f"जन्म तिथि / DOB: {printed_date(v['date_of_birth'], '/')}", font=hindi, fill=hard.INK)
    draw.text((270, 295), f"{gender_hi} / {gender_en}", font=hindi, fill=hard.INK)
    hard.qr_block(img, rng, 790, 130)
    number = v["aadhaar_number"]
    shown = f"{number[:4]} {number[4:8]} {number[8:]}"
    big = hard.font("arialbd.ttf", 54)
    draw.text(((1012 - draw.textlength(shown, font=big)) / 2, 430), shown, font=big, fill=hard.INK)
    draw.line((40, 530, 972, 530), fill=(200, 40, 40), width=3)
    draw.text((40, 548), "SAMPLE - fictional test document, not valid for any purpose",
              font=hard.font("arial.ttf", 22), fill=(160, 30, 30))
    return img


def draw_ration_card(who: dict[str, Any], v: dict[str, Any], rng: random.Random) -> Image.Image:
    img, draw = hard.new_page(1240, 900, (246, 248, 238))
    hard.guilloche(img, rng, (60, 130, 80), alpha=35)
    hard.watermark(img)
    y = hard.header(img, draw, ["FOOD & CIVIL SUPPLIES DEPARTMENT (SAMPLE)", "Ration Card - Public Distribution System"],
                    "राशन कार्ड (नमूना)")
    body = hard.font("times.ttf", 31)
    for line in (f"Ration Card No: {v['ration_card_number']}", f"Head of family: {v['head_of_family']}",
                 f"Card type: {CARD_WORDS[v['card_category']]}", f"State: {STATE_NAMES[v['residence_state']]}",
                 f"Family members: {v['family_members']}"):
        draw.text((100, y), line, font=body, fill=hard.INK)
        y += 58
    draw.text((100, y + 40), "SAMPLE - fictional test document, not valid for any purpose",
              font=hard.font("arial.ttf", 22), fill=(160, 30, 30))
    return img


def draw_bank_passbook(who: dict[str, Any], v: dict[str, Any], rng: random.Random) -> Image.Image:
    img, draw = hard.new_page(1240, 1000, (250, 248, 240))
    hard.guilloche(img, rng, (60, 90, 160), alpha=30)
    draw.text((60, 40), v["bank_name"].upper(), font=hard.font("georgiab.ttf", 44), fill=hard.NAVY)
    draw.text((60, 100), "Branch: Demo Nagar  |  SAMPLE PASSBOOK", font=hard.font("georgia.ttf", 26), fill=hard.NAVY)
    rows = [("Name", v["name"]), ("A/c No.", v["account_number"]), ("IFSC Code", v["ifsc"]),
            ("CIF No.", str(rng.randint(10 ** 9, 10 ** 10 - 1))),
            ("Date of Opening", f"{rng.randint(1, 28):02d}/{rng.randint(1, 12):02d}/{rng.randint(1995, 2015)}"),
            ("Nominee", "Registered")]
    label_font, value_font = hard.font("cour.ttf", 28), hard.font("courbd.ttf", 28)
    for i, (label, value) in enumerate(rows):
        y = 190 + i * 62
        draw.text((60, y), label + " " + "." * max(2, 18 - len(label)), font=label_font, fill=hard.INK)
        draw.text((420, y), ": " + value, font=value_font, fill=hard.INK)
    hard.photo_box(img, (930, 180, 1130, 420))
    hard.stamp(img, rng, (1010, 430), 85, ["DEMO BANK", "SAMPLE"])
    y = 600
    columns = (70, 260, 640, 820, 1000)
    draw.line((60, y, 1180, y), fill=hard.INK, width=2)
    for x, head in zip(columns, ("Date", "Particulars", "Withdrawal", "Deposit", "Balance")):
        draw.text((x, y + 10), head, font=hard.font("arialbd.ttf", 24), fill=hard.INK)
    draw.line((60, y + 48, 1180, y + 48), fill=hard.INK, width=1)
    balance = rng.randint(40000, 90000)  # 4 withdrawals of up to 9000 never go below zero
    for r in range(4):
        amount = rng.randint(500, 9000)
        deposit = rng.random() < 0.5
        balance += amount if deposit else -amount
        cells = [f"{rng.randint(1, 28):02d}/0{r + 1}/26",
                 rng.choice(["BY CASH", "PENSION CREDIT"] if deposit else ["ATM WDL", "UPI/DEMO"]),
                 "" if deposit else f"{amount:.2f}", f"{amount:.2f}" if deposit else "", f"{balance:.2f}"]
        for x, cell in zip(columns, cells):
            draw.text((x, y + 62 + r * 44), cell, font=hard.font("cour.ttf", 24), fill=hard.INK)
    draw.text((60, 900), "SAMPLE - fictional test document, not valid for any purpose",
              font=hard.font("arial.ttf", 22), fill=(160, 30, 30))
    return img


def draw_death_certificate(who: dict[str, Any], v: dict[str, Any], rng: random.Random) -> Image.Image:
    img, draw = hard.new_page(1240, 1754)
    hard.guilloche(img, rng, (110, 110, 160), alpha=35)
    hard.watermark(img)
    y = hard.header(img, draw, ["GOVERNMENT OF DEMO STATE (SAMPLE)",
                                "Department of Health - Registration of Births & Deaths"], "मृत्यु प्रमाण पत्र (नमूना)")
    title = hard.font("timesbd.ttf", 38)
    draw.text(((1240 - draw.textlength("DEATH CERTIFICATE", font=title)) / 2, y), "DEATH CERTIFICATE", font=title,
              fill=hard.INK)
    body = hard.font("times.ttf", 31)
    lines = [f"Name of deceased: Late {v['deceased_name']}", f"Date of death: {printed_date(v['date_of_death'], '/')}",
             f"Age: {v['age_at_death']} years", f"Place of death: {v['place_of_death']}",
             f"Name of husband/wife: {v['spouse_name']}", f"Registration No: {v['registration_number']}",
             f"Date of issue: {printed_date(v['issue_date'], '-')}", f"Issuing authority: {v['issuing_authority']}"]
    y += 90
    for line in lines:
        draw.text((100, y), line, font=body, fill=hard.INK)
        y += 58
    hard.signature(img, rng, 820, y + 40, "Registrar")
    hard.stamp(img, rng, (rng.randint(330, 900), y + 90), 100, ["REGISTRAR", "SAMPLE", "DEATHS"])
    draw.text((100, y + 260), "SAMPLE - fictional test document, not valid for any purpose",
              font=hard.font("arial.ttf", 22), fill=(160, 30, 30))
    return img


def draw_disability_certificate(who: dict[str, Any], v: dict[str, Any], rng: random.Random,
                                age_then: int) -> Image.Image:
    img, draw = hard.new_page(1240, 1754)
    hard.watermark(img)
    y = hard.header(img, draw, ["MEDICAL BOARD, DEMO DISTRICT HOSPITAL",
                                "Certificate for Person with Disability (SAMPLE)"], "चिकित्सा बोर्ड (नमूना)")
    hard.photo_box(img, (960, y, 1140, y + 220))
    percent = f"{v['disability_percent']}%"
    text = (f"This is to certify that we have carefully examined {who['title']} {v['name']}, aged {age_then} years, "
            f"and are satisfied that the applicant is a case of locomotor disability. The diagnosis is "
            f"osteoarthritis of both knees. The applicant has {percent} (in figures) permanent physical "
            f"impairment in relation to both lower limbs as per guidelines.")
    y = hard.draw_paragraph(draw, 100, y + 20, 820, text, hard.font("times.ttf", 31), keep=(v["name"], percent))
    y += 40
    for i, member in enumerate(["Member", "Member", "Chairperson"]):
        x = 100 + i * 360
        hard.signature(img, rng, x, y + 20, "Doctor")
        draw.text((x, y + 110), member, font=hard.font("times.ttf", 26), fill=hard.INK)
    hard.stamp(img, rng, (rng.randint(300, 900), y + 60), 100, ["DISTRICT", "HOSPITAL", "SAMPLE"])
    draw.text((100, y + 200), f"Issued by: {v['issuing_authority']}", font=hard.font("times.ttf", 30), fill=hard.INK)
    draw.text((100, y + 250), f"Date: {printed_date(v['issue_date'], '.')}", font=hard.font("times.ttf", 30),
              fill=hard.INK)
    draw.text((100, y + 330), "SAMPLE - fictional test document, not valid for any purpose",
              font=hard.font("arial.ttf", 22), fill=(160, 30, 30))
    return img


DRAWERS = {"identity": draw_identity, "ration_card": draw_ration_card, "bank_passbook": draw_bank_passbook,
           "death_certificate": draw_death_certificate, "disability_certificate": draw_disability_certificate}


def draw(who: dict[str, Any], item: dict[str, Any]) -> Image.Image:
    rng = random.Random(f"{SEED}:{who['key']}:{item['file']}")  # only the clutter is random
    extra = {"age_then": item["age_then"]} if "age_then" in item else {}
    return DRAWERS[item["type"]](who, item["values"], rng, **extra)


def generate(out: Path = OUT) -> dict[str, Any]:
    """Draws every page into out/<person>/ and writes out/answers.json. Gives back the answers."""
    answers: dict[str, Any] = {"note": "Made-up people and papers, for trying the benefits check. Values are as "
                                       "they should be saved after the check step.", "people": []}
    for who in PEOPLE:
        folder = out / who["folder"]
        folder.mkdir(parents=True, exist_ok=True)
        for item in who["papers"]:
            draw(who, item).convert("RGB").save(folder / item["file"])
        answers["people"].append({
            "folder": who["folder"], "name": who["name"],
            "papers": [{"file": item["file"], "type": item["type"], "extra": bool(item.get("extra")),
                        "values": item["values"]} for item in who["papers"]],
        })
    (out / "answers.json").write_text(json.dumps(answers, indent=2, ensure_ascii=False), encoding="utf-8")
    return answers


if __name__ == "__main__":
    result = generate()
    count = sum(len(item["papers"]) for item in result["people"])
    print(f"{count} pages written to {OUT}")
