"""Generate a harder fake-document OCR dataset: realistic layouts + capture damage.

Why: the first benchmark pages were all "Label: value" lines in clean English, which
is exactly what the parser expects. Real documents are not like that. These pages have:
  - layouts: unlabelled values (ID card), paragraphs (certificates), label above value
    (insurance card), dotted leaders (passbook), tables (lab report), Hindi + English,
    DD/MM/YYYY and 31-Mar-2027 dates, "Rs. 2,66,000/-" amounts
  - clutter: background patterns, watermark, rubber stamps, signatures, photo, QR block
  - capture damage (OpenCV/NumPy only): scan, photocopy, phone photo, shadow + dim,
    glare + shake, worn paper (folds, stain, faded ink)

Everything is made up and every page says SAMPLE. The output folder is git-ignored.

Run from backend/:
    .venv/Scripts/python.exe tests/make_hard_dataset.py            # generate
    .venv/Scripts/python.exe tests/make_hard_dataset.py --score    # + run PaddleOCR and score
    .venv/Scripts/python.exe tests/make_hard_dataset.py --rescore  # score again from saved OCR text
    .venv/Scripts/python.exe tests/make_hard_dataset.py --rescore --llm  # ... plus the model (Groq)
"""

from __future__ import annotations

import json
import math
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
from backend.utils.privacy import verhoeff_check_digit  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent / "datasets" / "ocr_hard"
FONT_DIR = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
NBSP = "\u00a0"

TYPES = ["identity", "income_certificate", "residence_proof", "health_insurance",
         "bank_passbook", "disability_certificate", "lab_report", "prescription", "medical_bill"]
CONDITIONS = ["scan", "photocopy", "phone_photo", "phone_shadow_dim", "phone_glare_shake", "worn"]

PEOPLE = [
    {"name": "Meera Rao", "hindi": "मीरा राव", "gender": "FEMALE", "hindi_gender": "महिला", "title": "Smt."},
    {"name": "Suresh Kumar Iyer", "hindi": "सुरेश कुमार अय्यर", "gender": "MALE", "hindi_gender": "पुरुष", "title": "Shri"},
    {"name": "Anita Deshpande", "hindi": "अनीता देशपांडे", "gender": "FEMALE", "hindi_gender": "महिला", "title": "Smt."},
    {"name": "Ramesh Chandra Gupta", "hindi": "रमेश चंद्र गुप्ता", "gender": "MALE", "hindi_gender": "पुरुष", "title": "Shri"},
    {"name": "Lakshmi Narayanan", "hindi": "लक्ष्मी नारायणन", "gender": "FEMALE", "hindi_gender": "महिला", "title": "Smt."},
]
STATES = {"Maharashtra": "MH", "Karnataka": "KA", "Tamil Nadu": "TN", "Gujarat": "GJ", "Kerala": "KL"}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
INK = (28, 28, 40)
NAVY = (20, 40, 110)


# ---------- fonts and text ----------

def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    """A Windows font by file name; Pillow's built-in font if it is missing."""
    try:
        return ImageFont.truetype(str(FONT_DIR / name), size)
    except OSError:
        return ImageFont.load_default(size=size)


def draw_paragraph(draw, x, y, width, text, fnt, fill=INK, gap=1.55, keep=()):
    """Wrap text to width. Phrases in `keep` are never split across lines."""
    for phrase in keep:
        text = text.replace(phrase, phrase.replace(" ", NBSP))
    lines, current = [], ""
    for word in text.split(" "):
        trial = f"{current} {word}".strip()
        if not current or draw.textlength(trial.replace(NBSP, " "), font=fnt) <= width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    for line in lines:
        draw.text((x, y), line.replace(NBSP, " "), font=fnt, fill=fill)
        y += int(fnt.size * gap)
    return y


# ---------- fake values ----------

def fake_aadhaar(rng: random.Random) -> str:
    body = str(rng.randint(2, 9)) + "".join(str(rng.randint(0, 9)) for _ in range(10))
    number = body + verhoeff_check_digit(body)
    return f"{number[:4]} {number[4:8]} {number[8:]}"


def fake_pan(rng: random.Random) -> str:
    letters = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    return ("".join(rng.choice(letters) for _ in range(3)) + "P" + rng.choice(letters)
            + "".join(str(rng.randint(0, 9)) for _ in range(4)) + rng.choice(letters))


def inr(amount: int) -> str:
    """Indian digit grouping: 266000 -> 2,66,000."""
    text = str(amount)
    if len(text) <= 3:
        return text
    head, tail = text[:-3], text[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups + [tail])


ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven",
        "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def words_below_100(n: int) -> str:
    return ONES[n] if n < 20 else (TENS[n // 10] + (" " + ONES[n % 10] if n % 10 else ""))


def rupees_in_words(n: int) -> str:
    """Indian words: 266000 -> 'Two Lakh Sixty Six Thousand'."""
    if n == 0:
        return "Zero"
    parts = []
    for size, word in ((10_000_000, "Crore"), (100_000, "Lakh"), (1000, "Thousand"), (100, "Hundred")):
        if n >= size:
            parts.append(f"{words_below_100(n // size)} {word}")
            n %= size
    if n:
        parts.append(words_below_100(n))
    return " ".join(parts)


def fake_date(rng: random.Random, start: int, end: int) -> tuple[int, int, int]:
    return rng.randint(start, end), rng.randint(1, 12), rng.randint(1, 28)


def iso(d):
    return f"{d[0]:04d}-{d[1]:02d}-{d[2]:02d}"


def field(shown: str, value: Any) -> dict[str, Any]:
    """shown = exactly what is printed; value = what the vault should end up with."""
    return {"shown": shown, "value": value}


# ---------- clutter ----------

def guilloche(img: Image.Image, rng: random.Random, color, box=None, alpha=60):
    """Thin wavy security-style lines over the background."""
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    x0, y0, x1, y1 = box or (0, 0, img.width, img.height)
    for k in range(18):
        amp = rng.uniform(8, 30)
        freq = rng.uniform(0.008, 0.03)
        phase = rng.uniform(0, 6.3)
        base = y0 + (y1 - y0) * k / 18
        points = [(x, base + amp * math.sin(freq * x + phase)) for x in range(x0, x1, 6)]
        draw.line(points, fill=(*color, alpha), width=1)
    img.alpha_composite(layer)


def watermark(img: Image.Image, text="SAMPLE", alpha=28):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    fnt = font("arialbd.ttf", int(img.width * 0.16))
    w = draw.textlength(text, font=fnt)
    draw.text(((img.width - w) / 2, img.height * 0.4), text, font=fnt, fill=(120, 120, 120, alpha))
    img.alpha_composite(layer.rotate(28, center=(img.width / 2, img.height / 2)))


def stamp(img: Image.Image, rng: random.Random, center, radius, lines, color=(70, 60, 170)):
    """Round rubber stamp, rotated, semi-transparent, may cover printed text."""
    size = radius * 2 + 20
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    alpha = rng.randint(150, 210)
    c = size // 2
    draw.ellipse((c - radius, c - radius, c + radius, c + radius), outline=(*color, alpha), width=5)
    draw.ellipse((c - radius + 14, c - radius + 14, c + radius - 14, c + radius - 14), outline=(*color, alpha), width=2)
    fnt = font("arialbd.ttf", max(12, radius // 5))
    for i, line in enumerate(lines):
        w = draw.textlength(line, font=fnt)
        draw.text((c - w / 2, c - len(lines) * fnt.size * 0.6 + i * fnt.size * 1.2), line, font=fnt, fill=(*color, alpha))
    layer = layer.rotate(rng.uniform(-35, 35), resample=Image.BICUBIC)
    img.alpha_composite(layer, (int(center[0] - size / 2), int(center[1] - size / 2)))


def signature(img: Image.Image, rng: random.Random, x, y, name, color=(20, 40, 150)):
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.text((x, y), name.split()[0], font=font("Inkfree.ttf", 46), fill=(*color, 230))
    pts = [(x + i * 14, y + 55 + rng.uniform(-10, 10)) for i in range(16)]
    draw.line(pts, fill=(*color, 210), width=3)
    img.alpha_composite(layer)


def photo_box(img: Image.Image, box):
    """Grey passport-photo placeholder (head and shoulders), drawn separately so nothing spills out."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    photo = Image.new("RGBA", (w, h), (196, 200, 205, 255))
    draw = ImageDraw.Draw(photo)
    draw.ellipse((w * 0.3, h * 0.12, w * 0.7, h * 0.5), fill=(150, 150, 158))
    draw.ellipse((w * 0.12, h * 0.55, w * 0.88, h * 1.3), fill=(150, 150, 158))
    draw.rectangle((0, 0, w - 1, h - 1), outline=(120, 120, 120), width=2)
    img.alpha_composite(photo, (x0, y0))


def qr_block(img: Image.Image, rng: random.Random, x, y, cell=7, n=25):
    draw = ImageDraw.Draw(img)
    for i in range(n):
        for j in range(n):
            if rng.random() < 0.5:
                draw.rectangle((x + i * cell, y + j * cell, x + (i + 1) * cell - 1, y + (j + 1) * cell - 1), fill=(20, 20, 20))


def new_page(width, height, tint=(252, 251, 246)) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGBA", (width, height), (*tint, 255))
    return img, ImageDraw.Draw(img)


def header(img, draw, lines_en, line_hi, y=60):
    big, small = font("timesbd.ttf", 40), font("Nirmala.ttc", 30)
    w = draw.textlength(line_hi, font=small)
    draw.text(((img.width - w) / 2, y), line_hi, font=small, fill=NAVY)
    y += 50
    for i, line in enumerate(lines_en):
        fnt = big if i == 0 else font("times.ttf", 30)
        w = draw.textlength(line, font=fnt)
        draw.text(((img.width - w) / 2, y), line, font=fnt, fill=NAVY)
        y += int(fnt.size * 1.4)
    draw.line((80, y + 10, img.width - 80, y + 10), fill=NAVY, width=3)
    return y + 40


# ---------- layouts ----------

def make_identity(rng, person):
    """Card with no 'Name:' label, Hindi + English, number printed alone at the bottom."""
    img, draw = new_page(1012, 638, (247, 244, 236))
    guilloche(img, rng, (200, 120, 60))
    draw.rectangle((0, 0, 1012, 90), fill=(236, 120, 40))
    draw.text((40, 12), "नमूना पहचान पत्र", font=font("Nirmala.ttc", 30), fill=(255, 255, 255))
    draw.text((40, 50), "SAMPLE IDENTITY CARD", font=font("arialbd.ttf", 28), fill=(255, 255, 255))
    photo_box(img, (40, 130, 230, 360))
    dob = fake_date(rng, 1940, 1962)
    dob_shown = f"{dob[2]:02d}/{dob[1]:02d}/{dob[0]}"
    aadhaar = fake_aadhaar(rng)
    hi = font("Nirmala.ttc", 30)
    draw.text((270, 135), person["hindi"], font=hi, fill=INK)
    draw.text((270, 185), person["name"], font=font("arial.ttf", 32), fill=INK)
    draw.text((270, 240), f"जन्म तिथि / DOB: {dob_shown}", font=hi, fill=INK)
    draw.text((270, 295), f"{person['hindi_gender']} / {person['gender']}", font=hi, fill=INK)
    qr_block(img, rng, 790, 130)
    w = draw.textlength(aadhaar, font=font("arialbd.ttf", 54))
    draw.text(((1012 - w) / 2, 430), aadhaar, font=font("arialbd.ttf", 54), fill=INK)
    draw.line((40, 530, 972, 530), fill=(200, 40, 40), width=3)
    draw.text((40, 548), "SAMPLE - fictional test document, not valid for any purpose",
              font=font("arial.ttf", 22), fill=(160, 30, 30))
    fields = {"name": field(person["name"], person["name"]),
              "date_of_birth": field(dob_shown, iso(dob)),
              "aadhaar_number": field(aadhaar, aadhaar.replace(" ", ""))}
    return img, fields, []


def certificate_tail(img, draw, rng, y, office, date_text):
    body = font("times.ttf", 30)
    draw.text((100, y), f"Place: Demo Taluka", font=body, fill=INK)
    draw.text((100, y + 50), f"Date: {date_text}", font=body, fill=INK)
    signature(img, rng, 820, y - 10, "Officer")
    draw.text((780, y + 70), office, font=font("timesbd.ttf", 28), fill=INK)
    stamp(img, rng, (rng.randint(330, 900), y + rng.randint(-20, 60)), 105, ["DEMO TALUKA", "SAMPLE", "OFFICE"])


def make_income_certificate(rng, person):
    """Paragraph certificate: amount as 'Rs. 2,66,000/-' with words, date DD/MM/YYYY."""
    img, draw = new_page(1240, 1754)
    guilloche(img, rng, (90, 140, 90), alpha=35)
    watermark(img)
    y = header(img, draw, ["OFFICE OF THE TAHSILDAR (SAMPLE)", "Demo Taluka, Demo District"], "कार्यालय तहसीलदार (नमूना)")
    title = font("timesbd.ttf", 38)
    w = draw.textlength("INCOME CERTIFICATE", font=title)
    draw.text(((1240 - w) / 2, y), "INCOME CERTIFICATE", font=title, fill=INK)
    no = f"No. INC/{rng.randint(2024, 2026)}/{rng.randint(1000, 99999)}"
    draw.text((100, y + 80), no, font=font("times.ttf", 28), fill=INK)
    income = rng.randrange(60_000, 300_000, 1000)
    issued = fake_date(rng, 2025, 2026)
    issued_shown = f"{issued[2]:02d}/{issued[1]:02d}/{issued[0]}"
    state = rng.choice(list(STATES))
    amount = f"Rs. {inr(income)}/-"
    text = (f"This is to certify that {person['title']} {person['name']}, resident of House No. {rng.randint(1, 300)}, "
            f"Demo Nagar, Demo Taluka, {state}, has an annual income of {amount} "
            f"(Rupees {rupees_in_words(income)} only) from all sources for the financial year 2025-26, "
            f"as per the enquiry made and the documents produced by the applicant.")
    y = draw_paragraph(draw, 100, y + 150, 1040, text, font("times.ttf", 32), keep=(person["name"], amount))
    y = draw_paragraph(draw, 100, y + 30, 1040, "This certificate is issued on the request of the applicant "
                       "for the purpose of availing benefits under government schemes.", font("times.ttf", 32))
    certificate_tail(img, draw, rng, y + 120, "Tahsildar, Demo Taluka", issued_shown)
    fields = {"name": field(person["name"], person["name"]),
              "annual_income": field(inr(income), income),
              "issue_date": field(issued_shown, iso(issued))}
    return img, fields, []


def make_residence_proof(rng, person):
    """Domicile certificate: state written as a full name inside a sentence."""
    img, draw = new_page(1240, 1754)
    guilloche(img, rng, (120, 90, 150), alpha=35)
    watermark(img)
    y = header(img, draw, ["OFFICE OF THE SUB-DIVISIONAL OFFICER (SAMPLE)", "Demo Sub-Division"], "उपविभागीय अधिकारी कार्यालय (नमूना)")
    title = font("timesbd.ttf", 38)
    w = draw.textlength("DOMICILE CERTIFICATE", font=title)
    draw.text(((1240 - w) / 2, y), "DOMICILE CERTIFICATE", font=title, fill=INK)
    state = rng.choice(list(STATES))
    issued = fake_date(rng, 2025, 2026)
    issued_shown = f"{issued[2]:02d}-{issued[1]:02d}-{issued[0]}"
    since = rng.randint(1965, 1990)
    text = (f"Certified that {person['title']} {person['name']}, born on {rng.randint(1, 28)}/{rng.randint(1, 12)}/"
            f"{rng.randint(1940, 1962)}, has been residing in the State of {state} since {since} and is a domicile "
            f"of the State of {state}. This certificate is issued after verification of the records.")
    y = draw_paragraph(draw, 100, y + 120, 1040, text, font("times.ttf", 32), keep=(person["name"], state))
    certificate_tail(img, draw, rng, y + 160, "Sub-Divisional Officer", issued_shown)
    fields = {"name": field(person["name"], person["name"]),
              "residence_state": field(state, STATES[state]),
              "issue_date": field(issued_shown, iso(issued))}
    return img, fields, []


def make_health_insurance(rng, person):
    """Card with small grey labels ABOVE each value, dates like 31-Mar-2027."""
    img, draw = new_page(1012, 638, (255, 255, 255))
    draw.rectangle((0, 0, 1012, 110), fill=(0, 110, 120))
    insurer = "Demo Health Insurance Co. Ltd."
    draw.text((30, 20), insurer, font=font("arialbd.ttf", 36), fill=(255, 255, 255))
    draw.text((30, 68), "Cashless Health Card  |  SAMPLE", font=font("arial.ttf", 24), fill=(220, 240, 240))
    guilloche(img, rng, (0, 110, 120), box=(0, 110, 1012, 638), alpha=30)
    policy = f"HLT/{rng.randint(2024, 2026)}/{rng.randint(100000, 999999)}"
    sum_insured = rng.choice([200000, 300000, 500000, 1000000])
    end = (rng.randint(2027, 2029), rng.randint(1, 12), 28)
    end_shown = f"{end[2]:02d}-{MONTHS[end[1] - 1]}-{end[0]}"
    label, value = font("arial.ttf", 20), font("arialbd.ttf", 30)
    cells = [("Member Name", person["name"]), ("Policy Number", policy),
             ("Member ID", f"MID{rng.randint(10000000, 99999999)}"), ("Sum Insured", f"₹ {inr(sum_insured)}"),
             ("Valid From", f"01-{MONTHS[end[1] - 1]}-{end[0] - 1}"), ("Valid Upto", end_shown)]
    for i, (lab, val) in enumerate(cells):
        x, y = 40 + (i % 2) * 480, 150 + (i // 2) * 120
        draw.text((x, y), lab, font=label, fill=(120, 120, 120))
        draw.text((x, y + 28), val, font=value, fill=INK)
    draw.text((40, 540), "Toll free: 1800-000-0000 (demo)   Fictional test card - not valid", font=font("arial.ttf", 20), fill=(90, 90, 90))
    fields = {"name": field(person["name"], person["name"]),
              "insurer": field(insurer, insurer),
              "policy_number": field(policy, policy),
              "expiry_date": field(end_shown, iso(end)),
              "sum_insured": field(inr(sum_insured), sum_insured)}
    return img, fields, []


def make_bank_passbook(rng, person):
    """Passbook first page: dotted leaders, two columns, photo + stamp, small transaction table."""
    img, draw = new_page(1240, 1000, (250, 248, 240))
    guilloche(img, rng, (60, 90, 160), alpha=30)
    bank = "Demo Co-operative Bank Ltd."
    draw.text((60, 40), bank.upper(), font=font("georgiab.ttf", 44), fill=NAVY)
    draw.text((60, 100), "Branch: Demo Nagar  |  SAMPLE PASSBOOK", font=font("georgia.ttf", 26), fill=NAVY)
    account = "".join(str(rng.randint(0, 9)) for _ in range(rng.choice([11, 12, 14])))
    ifsc = "DEMO0" + "".join(str(rng.randint(0, 9)) for _ in range(6))
    rows = [("Name", person["name"]), ("A/c No.", account), ("IFSC Code", ifsc),
            ("CIF No.", str(rng.randint(10 ** 9, 10 ** 10 - 1))), ("Date of Opening", f"{rng.randint(1, 28):02d}/{rng.randint(1, 12):02d}/{rng.randint(1995, 2015)}"),
            ("Nominee", "Registered")]
    lab, val = font("cour.ttf", 28), font("courbd.ttf", 28)
    for i, (label, value) in enumerate(rows):
        y = 190 + i * 62
        draw.text((60, y), label + " " + "." * max(2, 18 - len(label)), font=lab, fill=INK)
        draw.text((420, y), ": " + value, font=val, fill=INK)
    photo_box(img, (930, 180, 1130, 420))
    stamp(img, rng, (1010, 430), 85, ["DEMO BANK", "SAMPLE"])
    y = 600
    draw.line((60, y, 1180, y), fill=INK, width=2)
    for x, head in zip((70, 260, 640, 820, 1000), ("Date", "Particulars", "Withdrawal", "Deposit", "Balance")):
        draw.text((x, y + 10), head, font=font("arialbd.ttf", 24), fill=INK)
    draw.line((60, y + 48, 1180, y + 48), fill=INK, width=1)
    balance = rng.randint(40000, 90000)  # 4 withdrawals of up to 9000 never go below zero
    for r in range(4):
        amount = rng.randint(500, 9000)
        deposit = rng.random() < 0.5
        balance += amount if deposit else -amount
        cells = [f"{rng.randint(1, 28):02d}/0{r + 1}/26", rng.choice(["BY CASH", "PENSION CREDIT"] if deposit else ["ATM WDL", "UPI/DEMO"]),
                 "" if deposit else f"{amount:.2f}", f"{amount:.2f}" if deposit else "", f"{balance:.2f}"]
        for x, cell in zip((70, 260, 640, 820, 1000), cells):
            draw.text((x, y + 62 + r * 44), cell, font=font("cour.ttf", 24), fill=INK)
    fields = {"name": field(person["name"], person["name"]),
              "bank_name": field(bank.upper(), bank),
              "account_number": field(account, account),
              "ifsc": field(ifsc, ifsc)}
    return img, fields, []


def make_disability_certificate(rng, person):
    """Board certificate: percentage inside a sentence, date with dots."""
    img, draw = new_page(1240, 1754)
    watermark(img)
    y = header(img, draw, ["MEDICAL BOARD, DEMO DISTRICT HOSPITAL", "Certificate for Person with Disability (SAMPLE)"], "चिकित्सा बोर्ड (नमूना)")
    photo_box(img, (960, y, 1140, y + 220))
    percent = rng.choice([40, 45, 50, 60, 75, 80])
    issued = fake_date(rng, 2025, 2026)
    issued_shown = f"{issued[2]:02d}.{issued[1]:02d}.{issued[0]}"
    authority = "Medical Board, Demo District Hospital"
    text = (f"This is to certify that we have carefully examined {person['title']} {person['name']}, aged "
            f"{rng.randint(62, 85)} years, and are satisfied that the applicant is a case of locomotor disability. "
            f"The diagnosis is osteoarthritis of both knees. The applicant has {percent}% (in figures) permanent "
            f"physical impairment in relation to both lower limbs as per guidelines.")
    y = draw_paragraph(draw, 100, y + 20, 820, text, font("times.ttf", 31), keep=(person["name"], f"{percent}%"))
    y += 40
    for i, member in enumerate(["Member", "Member", "Chairperson"]):
        x = 100 + i * 360
        signature(img, rng, x, y + 20, "Doctor")
        draw.text((x, y + 110), member, font=font("times.ttf", 26), fill=INK)
    stamp(img, rng, (rng.randint(300, 900), y + 60), 100, ["DISTRICT", "HOSPITAL", "SAMPLE"])
    draw.text((100, y + 200), f"Issued by: {authority}", font=font("times.ttf", 30), fill=INK)
    draw.text((100, y + 250), f"Date: {issued_shown}", font=font("times.ttf", 30), fill=INK)
    fields = {"name": field(person["name"], person["name"]),
              "issuing_authority": field(authority, authority),
              "issue_date": field(issued_shown, iso(issued)),
              "disability_percent": field(f"{percent}%", percent)}
    return img, fields, []


LAB_TESTS = [("Haemoglobin", "g/dL", "12.0 - 15.5", 9.0, 16.0), ("Fasting Blood Sugar", "mg/dL", "70 - 100", 80, 180),
             ("HbA1c", "%", "4.0 - 5.6", 5.0, 9.5), ("Serum Creatinine", "mg/dL", "0.6 - 1.2", 0.6, 2.2),
             ("Total Cholesterol", "mg/dL", "< 200", 150, 280), ("TSH", "uIU/mL", "0.4 - 4.0", 0.5, 7.0)]


def make_lab_report(rng, person):
    """Two-column patient block + a ruled results table with H/L flags."""
    img, draw = new_page(1240, 1500, (255, 255, 255))
    draw.rectangle((0, 0, 1240, 130), fill=(235, 242, 250))
    draw.text((60, 30), "DEMO DIAGNOSTICS & PATHOLOGY LAB", font=font("arialbd.ttf", 40), fill=NAVY)
    draw.text((60, 85), "NABL-style sample report  |  SAMPLE - fictional", font=font("arial.ttf", 22), fill=NAVY)
    collected = fake_date(rng, 2026, 2026)
    info = [("Patient Name", f"{person['title']} {person['name']}"), ("Age / Sex", f"{rng.randint(62, 88)} Y / {person['gender'][0]}"),
            ("Ref. By", "Dr. Demo Physician"), ("Sample ID", f"LAB{rng.randint(100000, 999999)}"),
            ("Collected On", f"{collected[2]:02d}-{MONTHS[collected[1] - 1]}-{collected[0]} 08:15 AM"),
            ("Reported On", f"{collected[2]:02d}-{MONTHS[collected[1] - 1]}-{collected[0]} 06:40 PM")]
    lab, val = font("arial.ttf", 24), font("arialbd.ttf", 24)
    for i, (label, value) in enumerate(info):
        x, y = 60 + (i % 2) * 600, 170 + (i // 2) * 46
        draw.text((x, y), label, font=lab, fill=INK)
        draw.text((x + 180, y), ": " + value, font=val, fill=INK)
    y = 340
    columns = (60, 520, 720, 900)
    draw.rectangle((50, y, 1190, y + 50), fill=(225, 230, 238))
    for x, head in zip(columns, ("Test", "Result", "Unit", "Reference Range")):
        draw.text((x, y + 12), head, font=font("arialbd.ttf", 26), fill=INK)
    truth_rows = []
    for r, (name, unit, ref, low, high) in enumerate(rng.sample(LAB_TESTS, 5)):
        ry = y + 70 + r * 58
        result = round(rng.uniform(low, high), 1)
        draw.text((columns[0], ry), name, font=font("arial.ttf", 26), fill=INK)
        draw.text((columns[1], ry), str(result), font=font("arialbd.ttf", 26), fill=INK)
        draw.text((columns[2], ry), unit, font=font("arial.ttf", 26), fill=INK)
        draw.text((columns[3], ry), ref, font=font("arial.ttf", 26), fill=INK)
        draw.line((50, ry + 46, 1190, ry + 46), fill=(200, 200, 200), width=1)
        truth_rows.append([name, str(result), unit, ref])
    signature(img, rng, 880, y + 420, "Pathologist")
    draw.text((860, y + 500), "Consultant Pathologist", font=font("arial.ttf", 24), fill=INK)
    draw.text((60, y + 600), "*** End of Report ***", font=font("arial.ttf", 22), fill=(90, 90, 90))
    reported = f"{collected[2]:02d}-{MONTHS[collected[1] - 1]}-{collected[0]}"
    tests = [{"name": n, "value": float(v), "unit": u, "ref_range": r} for n, v, u, r in truth_rows]
    fields = {"name": field(person["name"], person["name"]),
              "lab_name": field("DEMO DIAGNOSTICS & PATHOLOGY LAB", "DEMO DIAGNOSTICS & PATHOLOGY LAB"),
              "report_date": field(reported, iso(collected)),
              "tests": field(truth_rows[0][0], tests)}
    return img, fields, truth_rows


MEDICINES = [("Tab", "Metformin", "500 mg"), ("Tab", "Amlodipine", "5 mg"), ("Tab", "Atorvastatin", "10 mg"),
             ("Cap", "Omeprazole", "20 mg"), ("Tab", "Telmisartan", "40 mg"), ("Syp", "Lactulose", "15 ml"),
             ("Tab", "Levothyroxine", "50 mcg")]
FREQUENCIES = ["1-0-1", "1-0-0", "0-0-1", "OD", "BD", "1-1-1"]
DOCTORS = ["Asha Menon", "Vikram Joshi", "Farah Khan", "Prakash Rao"]


def make_prescription(rng, person):
    """Printed clinic prescription: doctor header, patient line with several pairs, Rx list."""
    img, draw = new_page(1240, 1500, (255, 255, 252))
    doctor = rng.choice(DOCTORS)
    draw.text((60, 40), f"Dr. {doctor}", font=font("georgiab.ttf", 46), fill=NAVY)
    draw.text((60, 100), "MBBS, MD (General Medicine)  |  Reg. No. DEMO-12345 (fictional)",
              font=font("georgia.ttf", 24), fill=NAVY)
    draw.text((60, 135), "Demo Family Clinic, Demo Nagar  |  SAMPLE", font=font("georgia.ttf", 24), fill=NAVY)
    draw.line((50, 180, 1190, 180), fill=NAVY, width=3)
    when = fake_date(rng, 2026, 2026)
    when_shown = f"{when[2]:02d}/{when[1]:02d}/{when[0]}"
    draw.text((60, 210), f"Patient: {person['title']} {person['name']}     Age/Sex: {rng.randint(62, 88)}/"
                         f"{person['gender'][0]}     Date: {when_shown}", font=font("arial.ttf", 28), fill=INK)
    draw.text((60, 290), "Rx", font=font("timesbd.ttf", 56), fill=INK)
    rows = []
    for index, (form, name, strength) in enumerate(rng.sample(MEDICINES, rng.randint(2, 4)), start=1):
        frequency, days = rng.choice(FREQUENCIES), rng.choice([5, 10, 15, 30, 60])
        text = f"{index}. {form}. {name} {strength} {frequency} x {days} days"
        draw.text((90, 380 + (index - 1) * 70), text, font=font("arial.ttf", 30), fill=INK)
        rows.append({"name": name, "form": form, "strength": strength, "frequency": frequency,
                     "duration": f"x {days} days"})
    y = 380 + len(rows) * 70 + 40
    draw.text((60, y), "Advice: Walk 30 minutes daily. Review after one month.", font=font("arial.ttf", 26), fill=INK)
    signature(img, rng, 860, y + 120, doctor)
    stamp(img, rng, (rng.randint(700, 1000), y + 160), 90, ["DEMO CLINIC", "SAMPLE"])
    fields = {"name": field(person["name"], person["name"]),
              "doctor_name": field(f"Dr. {doctor}", f"Dr. {doctor}"),
              "prescription_date": field(when_shown, iso(when)),
              "medicines": field(rows[0]["name"], rows)}
    return img, fields, []


BILL_ITEMS = [("Metformin 500mg (10 tabs)", 60.0), ("Amlodipine 5mg (10 tabs)", 45.5), ("Consultation fee", 500.0),
              ("Blood test - HbA1c", 450.0), ("Atorvastatin 10mg (10 tabs)", 98.25), ("Dressing charges", 150.0)]


def make_medical_bill(rng, person):
    """Hospital pharmacy bill: heading as provider, item table with qty/rate/amount, totals."""
    img, draw = new_page(1240, 1400, (255, 255, 255))
    provider = "DEMO CITY HOSPITAL PHARMACY"
    draw.text((60, 40), provider, font=font("arialbd.ttf", 42), fill=NAVY)
    draw.text((60, 95), "Demo Road, Demo City  |  GSTIN: DEMO0000 (fictional)  |  SAMPLE",
              font=font("arial.ttf", 22), fill=NAVY)
    when = fake_date(rng, 2026, 2026)
    when_shown = f"{when[2]:02d}/{when[1]:02d}/{when[0]}"
    draw.text((60, 160), f"Bill No: PH/2026/{rng.randint(1000, 9999)}", font=font("arial.ttf", 26), fill=INK)
    draw.text((700, 160), f"Bill Date: {when_shown}", font=font("arial.ttf", 26), fill=INK)
    draw.text((60, 210), f"Patient Name: {person['title']} {person['name']}", font=font("arial.ttf", 26), fill=INK)
    y = 290
    columns = (60, 140, 760, 880, 1040)
    draw.rectangle((50, y, 1190, y + 46), fill=(232, 236, 242))
    for x, head in zip(columns, ("S.No", "Description", "Qty", "Rate", "Amount")):
        draw.text((x, y + 10), head, font=font("arialbd.ttf", 24), fill=INK)
    rows, total = [], 0.0
    for index, (description, rate) in enumerate(rng.sample(BILL_ITEMS, rng.randint(2, 4)), start=1):
        qty = rng.randint(1, 3)
        amount = round(qty * rate, 2)
        total += amount
        ry = y + 60 + (index - 1) * 50
        for x, cell in zip(columns, (str(index), description, str(qty), f"{rate:.2f}", f"{amount:.2f}")):
            draw.text((x, ry), cell, font=font("arial.ttf", 24), fill=INK)
        rows.append({"description": description, "amount": amount})
    y = y + 60 + len(rows) * 50 + 20
    draw.line((50, y, 1190, y), fill=INK, width=1)
    total_shown = f"{inr(int(total))}.{round(total * 100) % 100:02d}"
    draw.text((760, y + 15), f"Sub Total  {total:.2f}", font=font("arial.ttf", 24), fill=INK)
    draw.text((700, y + 60), f"Grand Total: ₹ {total_shown}", font=font("arialbd.ttf", 30), fill=INK)
    draw.text((60, y + 130), f"Amount in words: Rupees {rupees_in_words(int(total))} only",
              font=font("arial.ttf", 22), fill=INK)
    signature(img, rng, 880, y + 200, "Cashier")
    stamp(img, rng, (rng.randint(300, 700), y + 230), 80, ["DEMO", "PHARMACY", "PAID"])
    fields = {"name": field(person["name"], person["name"]),
              "provider": field(provider, provider),
              "bill_date": field(when_shown, iso(when)),
              "total_amount": field(total_shown, round(total, 2)),
              "items": field(rows[0]["description"], rows)}
    return img, fields, []


MAKERS = {"identity": make_identity, "income_certificate": make_income_certificate,
          "residence_proof": make_residence_proof, "health_insurance": make_health_insurance,
          "bank_passbook": make_bank_passbook, "disability_certificate": make_disability_certificate,
          "lab_report": make_lab_report, "prescription": make_prescription, "medical_bill": make_medical_bill}


# ---------- capture damage (all on RGB uint8 arrays) ----------

def jpeg(arr, quality):
    ok, buf = cv2.imencode(".jpg", arr[:, :, ::-1], [cv2.IMWRITE_JPEG_QUALITY, quality])
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)[:, :, ::-1]


def noise(arr, rng, sigma):
    gen = np.random.default_rng(rng.randint(0, 2 ** 31))
    return np.clip(arr.astype(np.float32) + gen.normal(0, sigma, arr.shape), 0, 255).astype(np.uint8)


def rotate(arr, degrees, background=(255, 255, 255)):
    h, w = arr.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), degrees, 1.0)
    cos, sin = abs(m[0, 0]), abs(m[0, 1])
    nw, nh = int(h * sin + w * cos), int(h * cos + w * sin)
    m[0, 2] += nw / 2 - w / 2
    m[1, 2] += nh / 2 - h / 2
    return cv2.warpAffine(arr, m, (nw, nh), flags=cv2.INTER_LINEAR, borderValue=background)


def surface(rng, w, h):
    """A table/cloth texture to lay the paper on."""
    gen = np.random.default_rng(rng.randint(0, 2 ** 31))
    base = np.array(rng.choice([(120, 85, 55), (70, 75, 90), (150, 140, 125), (95, 60, 50)]), dtype=np.float32)
    grain = cv2.GaussianBlur(gen.normal(0, 25, (h, w)).astype(np.float32), (0, 0), sigmaX=12, sigmaY=2)
    tex = base[None, None, :] + grain[:, :, None]
    return np.clip(tex, 0, 255).astype(np.uint8)


def phone_perspective(arr, rng):
    """Paper photographed at an angle, lying on a table."""
    h, w = arr.shape[:2]
    pad_w, pad_h = int(w * 0.12), int(h * 0.12)
    out_w, out_h = w + 2 * pad_w, h + 2 * pad_h
    jitter = lambda s: rng.uniform(-0.06, 0.06) * s  # noqa: E731
    dst = np.float32([[pad_w + jitter(w), pad_h + jitter(h)], [pad_w + w + jitter(w), pad_h + jitter(h)],
                      [pad_w + w + jitter(w), pad_h + h + jitter(h)], [pad_w + jitter(w), pad_h + h + jitter(h)]])
    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    m = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(arr, m, (out_w, out_h))
    mask = cv2.warpPerspective(np.full((h, w), 255, np.uint8), m, (out_w, out_h))
    back = surface(rng, out_w, out_h)
    mask3 = (mask[:, :, None] / 255.0)
    return (warped * mask3 + back * (1 - mask3)).astype(np.uint8)


def light_gradient(arr, rng, low, high):
    h, w = arr.shape[:2]
    angle = rng.uniform(0, 2 * math.pi)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    t = (xx / w) * math.cos(angle) + (yy / h) * math.sin(angle)
    t = (t - t.min()) / (t.max() - t.min() + 1e-6)
    gain = low + (high - low) * t
    return np.clip(arr * gain[:, :, None], 0, 255).astype(np.uint8)


def hand_shadow(arr, rng, strength=0.5):
    h, w = arr.shape[:2]
    mask = np.zeros((h, w), np.float32)
    x = rng.randint(int(w * 0.4), int(w * 0.9))
    pts = np.array([[x, 0], [w, 0], [w, h], [x - rng.randint(0, w // 3), h]], np.int32)
    cv2.fillPoly(mask, [pts], 1.0)
    mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=w * 0.03)
    return np.clip(arr * (1 - strength * mask[:, :, None]), 0, 255).astype(np.uint8)


def glare(arr, rng):
    h, w = arr.shape[:2]
    mask = np.zeros((h, w), np.float32)
    center = (rng.randint(int(w * 0.25), int(w * 0.75)), rng.randint(int(h * 0.25), int(h * 0.75)))
    cv2.ellipse(mask, center, (int(w * 0.16), int(h * 0.09)), rng.uniform(0, 180), 0, 360, 1.0, -1)
    mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=w * 0.04)
    return np.clip(arr + 235 * mask[:, :, None], 0, 255).astype(np.uint8)


def motion_blur(arr, rng, length):
    kernel = np.zeros((length, length), np.float32)
    kernel[length // 2, :] = 1.0 / length
    rot = cv2.getRotationMatrix2D((length / 2 - 0.5, length / 2 - 0.5), rng.uniform(0, 180), 1.0)
    kernel = cv2.warpAffine(kernel, rot, (length, length))
    kernel /= kernel.sum()
    return cv2.filter2D(arr, -1, kernel)


def photocopy(arr, rng):
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY).astype(np.float32)
    gray = np.clip((gray - 60) * 1.5, 0, 255)  # harsh contrast, faint backgrounds vanish
    gray = cv2.erode(gray, np.ones((2, 2), np.uint8))  # toner spread: strokes get thicker
    gen = np.random.default_rng(rng.randint(0, 2 ** 31))
    speckle = gen.random(gray.shape) < 0.002
    gray[speckle] = 30
    for _ in range(2):  # toner streaks
        x = rng.randint(0, gray.shape[1] - 3)
        gray[:, x:x + 2] *= 0.75
    gray = cv2.GaussianBlur(gray, (0, 0), 0.6)
    return cv2.cvtColor(gray.astype(np.uint8), cv2.COLOR_GRAY2RGB)


def folds_and_stain(arr, rng):
    h, w = arr.shape[:2]
    out = arr.astype(np.float32)
    for _ in range(2):  # creases: a dark line with a lighter band beside it
        if rng.random() < 0.5:
            y = rng.randint(int(h * 0.25), int(h * 0.75))
            out[y - 1:y + 2] *= 0.7
            out[y + 2:y + 14] = np.minimum(out[y + 2:y + 14] * 1.08, 255)
        else:
            x = rng.randint(int(w * 0.25), int(w * 0.75))
            out[:, x - 1:x + 2] *= 0.7
            out[:, x + 2:x + 14] = np.minimum(out[:, x + 2:x + 14] * 1.08, 255)
    mask = np.zeros((h, w), np.float32)
    cv2.ellipse(mask, (rng.randint(0, w), rng.randint(0, h)), (int(w * 0.1), int(w * 0.08)), 0, 0, 360, 1.0, -1)
    mask = cv2.GaussianBlur(mask, (0, 0), sigmaX=w * 0.01) * 0.35
    stain = np.array([160, 110, 60], np.float32)
    out = out * (1 - mask[:, :, None]) + stain * mask[:, :, None]
    return np.clip(out, 0, 255).astype(np.uint8)


def faded_ink(arr, strength=0.35):
    thin = cv2.dilate(arr, np.ones((2, 2), np.uint8))  # light background grows into strokes
    return np.clip(thin + (255 - thin) * strength, 0, 255).astype(np.uint8)


def apply_condition(img: Image.Image, condition: str, rng: random.Random) -> np.ndarray:
    arr = np.asarray(img.convert("RGB")).copy()
    if condition == "scan":
        arr = rotate(arr, rng.uniform(-1.0, 1.0))
        arr = jpeg(noise(arr, rng, 3), 85)
    elif condition == "photocopy":
        arr = photocopy(rotate(arr, rng.uniform(-1.5, 1.5)), rng)
        arr = jpeg(arr, 75)
    elif condition == "phone_photo":
        arr = phone_perspective(arr, rng)
        arr = light_gradient(arr, rng, 0.75, 1.05)
        arr = cv2.GaussianBlur(arr, (0, 0), rng.uniform(0.8, 1.3))
        arr = jpeg(noise(arr, rng, 6), 55)
    elif condition == "phone_shadow_dim":
        arr = phone_perspective(arr, rng)
        arr = hand_shadow(arr, rng, 0.55)
        arr = light_gradient(arr, rng, 0.45, 0.75)
        arr = jpeg(noise(arr, rng, 9), 50)
    elif condition == "phone_glare_shake":
        arr = phone_perspective(arr, rng)
        arr = glare(arr, rng)
        arr = motion_blur(arr, rng, rng.choice([5, 7]))
        arr = jpeg(noise(arr, rng, 5), 55)
    elif condition == "worn":
        arr = faded_ink(arr)
        arr = folds_and_stain(arr, rng)
        arr = rotate(arr, rng.uniform(-2.5, 2.5))
        small = cv2.resize(arr, None, fx=0.7, fy=0.7, interpolation=cv2.INTER_AREA)  # low-resolution capture
        arr = jpeg(cv2.resize(small, (arr.shape[1], arr.shape[0]), interpolation=cv2.INTER_LINEAR), 60)
    else:
        raise ValueError(f"unknown condition {condition}")
    return arr


# ---------- generate ----------

def generate(out_dir: Path = OUT_DIR, seed: int = 2026, types=None, conditions=None) -> list[dict[str, Any]]:
    """Write pages/*.png and manifest.json. Same seed -> same files."""
    types = types or TYPES
    conditions = conditions or CONDITIONS
    pages_dir = out_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for t_index, doc_type in enumerate(types):
        doc_rng = random.Random(f"{seed}:{doc_type}")
        person = PEOPLE[(TYPES.index(doc_type) + seed) % len(PEOPLE)]
        base, fields, rows = MAKERS[doc_type](doc_rng, person)
        base.convert("RGB").save(pages_dir / f"{doc_type}__base.png")
        for condition in conditions:
            arr = apply_condition(base, condition, random.Random(f"{seed}:{doc_type}:{condition}"))
            name = f"{doc_type}__{condition}.png"
            Image.fromarray(arr).save(pages_dir / name)
            manifest.append({"path": f"pages/{name}", "type": doc_type, "condition": condition,
                             "fields": fields, "rows": rows})
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    return manifest


# ---------- score with the real OCR ----------

def squash(text: str) -> str:
    return " ".join(text.split()).lower()


def read_pattern(shown: str) -> str:
    """Regex for a printed value inside one OCR line.

    Case is ignored and a space may appear or vanish anywhere inside the value
    ("5439 0214 9647" = "543902149647"). The value must not touch another digit, so
    "2,66,000" inside "22,66,000" (a misread) or glued to the next value doesn't count.
    """
    chars = [re.escape(c) for c in squash(shown).replace(" ", "")]
    return r"(?<!\d)" + " ?".join(chars) + r"(?!\d)"


# List answers are compared row by row on the parts that matter (flags and positions are extra).
LIST_COMPARE = {"tests": ("name", "value", "unit", "ref_range"),
                "medicines": ("name", "strength", "frequency", "duration"),
                "items": ("description", "amount")}


def same_value(key: str, got: Any, want: Any) -> bool:
    if key in LIST_COMPARE:
        columns = LIST_COMPARE[key]
        return (isinstance(got, list) and len(got) == len(want)
                and all(tuple(g.get(c) for c in columns) == tuple(w.get(c) for c in columns)
                        for g, w in zip(got, want)))
    return got is not None and got == want


def score_page(entry: dict[str, Any], page: dict[str, Any], use_llm: bool = False) -> dict[str, Any]:
    """read   = the printed value appears in the OCR text (case and spacing ignored)
       parsed = the graph's steps (cleanup, parser + layout rules, normalizer, repair) give
                the right vault value
       review = fields that were right but the graph would send to review because they came
                from a layout guess, a repair (look-alike fix, trimmed text) or the model
       use_llm: also ask the model (Groq) for required fields the rules missed, as the graph
                does with use_llm=True. The holder's name is the known value that gets redacted.
                The pages are fictional, so this counts as consent for medical documents too."""
    from backend.tools.demo_tools import extract_fields_from_pages, normalize_field
    from backend.tools.medical import extract_lists, heading_fields
    from backend.tools.ocr_postprocess import clean_page, repair_value

    text = page["text"]  # "read" is judged on the raw OCR text
    lines = [squash(line) for line in text.splitlines()]
    cleaned = clean_page(page)
    extracted = extract_fields_from_pages([cleaned], entry.get("type"))  # same steps as the graph
    extracted.update(extract_lists([cleaned], entry.get("type")))
    extracted.update(heading_fields([cleaned], entry.get("type"), extracted))
    if use_llm:
        from backend.agents.llm_agents import extract_missing_fields
        from backend.tools.document_rules import REQUIRED_FIELDS

        missing = [k for k in sorted(REQUIRED_FIELDS.get(entry.get("type"), ())) if k not in extracted]
        if missing:
            known = {"NAME": entry["fields"]["name"]["shown"]} if "name" in entry["fields"] else None
            for key, value in extract_missing_fields(cleaned["text"], entry["type"], missing, known).items():
                extracted[key] = {"value": value, "layout": "llm"}
    result = {"read": {}, "parsed": {}, "review": [], "rows_ok": 0, "rows_total": len(entry["rows"])}
    for key, truth in entry["fields"].items():
        result["read"][key] = any(re.search(read_pattern(truth["shown"]), line) for line in lines)
        found = extracted.get(key)
        got = normalize_field(key, found["value"]) if found else None
        repaired = repair_value(key, found["value"]) if found and got is None and isinstance(found["value"], str) else None
        if repaired:
            got = repaired[1]
        result["parsed"][key] = same_value(key, got, truth["value"])  # nothing found is never right
        if result["parsed"][key] and (repaired or found.get("layout") or key in LIST_COMPARE):
            result["review"].append(key)
    for row in entry["rows"]:
        if any(all(re.search(read_pattern(cell), line) for cell in row) for line in lines):
            result["rows_ok"] += 1
    return result


def score(out_dir: Path = OUT_DIR) -> dict[str, Any]:
    from backend.tools.ocr import read_document

    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    pages = []
    for entry in manifest:
        started = time.time()
        page = read_document(out_dir / entry["path"], "image/png")[0]
        seconds = round(time.time() - started, 1)
        result = score_page(entry, page)
        pages.append({"path": entry["path"], "type": entry["type"], "condition": entry["condition"],
                      "seconds": seconds, "quality": page["quality"], "blurry": page["blurry"],
                      "sharpness": page["sharpness"], "text": page["text"], "lines": page["lines"], **result})
        print(f"{entry['path']:45s} {seconds:5.1f}s q={page['quality']:.2f} "
              f"read {sum(result['read'].values())}/{len(result['read'])} "
              f"parsed {sum(result['parsed'].values())}/{len(result['parsed'])}", flush=True)
    return save_results(out_dir, pages)


def rescore(out_dir: Path = OUT_DIR, use_llm: bool = False) -> dict[str, Any]:
    """Score again from the OCR text saved by score(): no OCR, takes a second."""
    manifest = {e["path"]: e for e in json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))}
    saved = json.loads((out_dir / "results_paddle.json").read_text(encoding="utf-8"))["pages"]
    pages = []
    for page in saved:
        ocr_page = {"page": 1, "quality": page["quality"], "blurry": page.get("blurry", False), "text": page["text"]}
        if "lines" in page:  # saved by newer runs: lets the column rules use each piece's position
            ocr_page["lines"] = page["lines"]
        pages.append({**page, **score_page(manifest[page["path"]], ocr_page, use_llm)})
    return save_results(out_dir, pages, "results_paddle_llm.json" if use_llm else "results_paddle.json")


def save_results(out_dir: Path, pages: list[dict[str, Any]], name: str = "results_paddle.json") -> dict[str, Any]:
    def rate(rows, kind):
        total = sum(len(r[kind]) for r in rows)
        return round(100 * sum(sum(r[kind].values()) for r in rows) / total) if total else None

    summary = {
        "pages": len(pages),
        "read_percent": rate(pages, "read"),
        "parsed_percent": rate(pages, "parsed"),
        "seconds_per_page": round(sum(p["seconds"] for p in pages[1:]) / max(1, len(pages) - 1), 1),
        "lab_rows": f"{sum(p['rows_ok'] for p in pages)}/{sum(p['rows_total'] for p in pages)}",
        "by_condition": {c: {"read": rate([p for p in pages if p["condition"] == c], "read"),
                             "parsed": rate([p for p in pages if p["condition"] == c], "parsed")}
                         for c in dict.fromkeys(p["condition"] for p in pages)},
        "by_type": {t: {"read": rate([p for p in pages if p["type"] == t], "read"),
                        "parsed": rate([p for p in pages if p["type"] == t], "parsed")}
                    for t in dict.fromkeys(p["type"] for p in pages)},
        "missed_reads": [f"{p['path']}: {k}" for p in pages for k, ok in p["read"].items() if not ok],
        "right_but_sent_to_review": sum(len(p.get("review", [])) for p in pages),
    }
    (out_dir / name).write_text(
        json.dumps({"summary": summary, "pages": pages}, indent=1, ensure_ascii=False), encoding="utf-8")
    return summary


if __name__ == "__main__":
    if "--rescore" in sys.argv:
        print(json.dumps(rescore(use_llm="--llm" in sys.argv), indent=1, ensure_ascii=False))
    else:
        made = generate()
        print(f"generated {len(made)} pages in {OUT_DIR}")
        if "--score" in sys.argv:
            print(json.dumps(score(), indent=1, ensure_ascii=False))
