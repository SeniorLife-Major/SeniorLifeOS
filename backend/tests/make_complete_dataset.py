"""Generate the dataset for the complete-project suite (run_complete_suite.py).

Everything is fictional and every page says SAMPLE. Written to tests/datasets/complete/
(git-ignored). New people and new numbers, so nothing here was used while the code was tuned.

What is made:
- documents/: pages for all 9 document types, with the answer for every field
  - "seen" layouts: the layouts of make_hard_dataset.py (the code was tuned on these), with
    new people and numbers, under capture damage (scan, phone photo, worn paper);
  - "unseen" layouts: a plain bordered form with label wording the code was NOT written for
    (a fair test of how it copes with a new layout);
  - PDFs: text PDFs (digital certificates) and scanned PDFs.
  Three "applicant packs" (identity + income + residence of one person) feed the
  documents -> benefits pipeline: one eligible, one income too high, one wrong state.
- privacy/cases.json: text with ID numbers to hide and look-alikes to keep.
- file_safety/: safe and unsafe files with the verdict each should get.
- manual_entry/cases.json, benefits/cases.json, proofs/cases.json: hand-written cases with
  the answer a correct system should give (written from the rules, not from the code's output).

Run from the repo root:
    backend/.venv/Scripts/python.exe backend/tests/make_complete_dataset.py
"""

from __future__ import annotations

import json
import random
import struct
import sys
import zlib
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

OUT = TESTS / "datasets" / "complete"
SEED = "complete-2026-10-27"
AS_OF = "2026-10-27"

PEOPLE = [
    {"name": "Kavita Sharma", "hindi": "कविता शर्मा", "gender": "FEMALE", "hindi_gender": "महिला", "title": "Smt."},
    {"name": "Mohan Lal Verma", "hindi": "मोहन लाल वर्मा", "gender": "MALE", "hindi_gender": "पुरुष", "title": "Shri"},
    {"name": "Farida Begum", "hindi": "फरीदा बेगम", "gender": "FEMALE", "hindi_gender": "महिला", "title": "Smt."},
    {"name": "Joseph D'Souza", "hindi": "जोसेफ डिसूज़ा", "gender": "MALE", "hindi_gender": "पुरुष", "title": "Shri"},
]
# Applicant packs for the documents -> benefits pipeline (Demo Senior Support: age >= 60,
# income <= 2,00,000, state MH). Everyone is born 1940-1962, so age is always met.
PACKS = {
    "eligible": {"person": 0, "income_ok": True, "state": "Maharashtra", "expected": "ready_for_manual_handoff"},
    "income_too_high": {"person": 1, "income_ok": False, "state": "Maharashtra", "expected": "not_eligible_in_demo"},
    "wrong_state": {"person": 2, "income_ok": True, "state": "Karnataka", "expected": "not_eligible_in_demo"},
}
OTHER_DOCUMENTS = [("health_insurance", 3), ("bank_passbook", 0), ("disability_certificate", 1),
                   ("lab_report", 2), ("prescription", 3), ("medical_bill", 0)]
SEEN_CONDITIONS = ["scan", "phone_photo"]
WORN_ONCE = "worn"  # one extra damaged copy of each type


# ---------- seen layouts ----------

def pack_document(doc_type: str, pack: dict[str, Any]) -> tuple[Image.Image, dict, list]:
    """A page from the tuned layouts whose values give the pack its benefits outcome."""
    person = PEOPLE[pack["person"]]
    for attempt in range(500):
        rng = random.Random(f"{SEED}:{doc_type}:{pack['person']}:{attempt}")
        img, fields, rows = hard.MAKERS[doc_type](rng, person)
        if doc_type == "income_certificate" and (fields["annual_income"]["value"] <= 200_000) != pack["income_ok"]:
            continue
        if doc_type == "residence_proof" and fields["residence_state"]["shown"] != pack["state"]:
            continue
        return img, fields, rows
    raise RuntimeError(f"no {doc_type} page fits the pack")


def seen_documents() -> list[dict[str, Any]]:
    docs = []
    for pack_name, pack in PACKS.items():
        for doc_type in ("identity", "income_certificate", "residence_proof"):
            img, fields, rows = pack_document(doc_type, pack)
            docs.append({"id": f"{pack_name}__{doc_type}", "type": doc_type, "person": PEOPLE[pack["person"]]["name"],
                         "pack": pack_name, "image": img, "fields": fields, "rows": rows})
    for doc_type, person in OTHER_DOCUMENTS:
        for attempt in range(50):  # a document dated after the test day would rightly be refused
            seed = f"{SEED}:{doc_type}" + (f":{attempt}" if attempt else "")
            img, fields, rows = hard.MAKERS[doc_type](random.Random(seed), PEOPLE[person])
            if all(str(v["value"]) <= AS_OF for k, v in fields.items() if k.endswith("date") and k != "expiry_date"):
                break
        docs.append({"id": f"other__{doc_type}", "type": doc_type, "person": PEOPLE[person]["name"], "pack": None,
                     "image": img, "fields": fields, "rows": rows})
    return docs


# ---------- unseen layout: a plain bordered form ----------

def fake_aadhaar(rng: random.Random) -> str:
    return hard.fake_aadhaar(rng)


def form_values(doc_type: str, rng: random.Random, person: dict[str, Any]) -> tuple[str, list, list]:
    """(title, [(label, shown, field, value)], table) for a plain form. Labels are worded
    differently from the tuned layouts on purpose."""
    name = person["name"]
    day, month, year = rng.randint(1, 28), rng.randint(1, 12), rng.randint(2025, 2026)
    issued = f"{day:02d}/{month:02d}/{year}"
    issued_iso = f"{year}-{month:02d}-{day:02d}"
    if doc_type == "identity":
        dob = (rng.randint(1941, 1960), rng.randint(1, 12), rng.randint(1, 28))
        aadhaar = fake_aadhaar(rng)
        return "PERSONAL IDENTIFICATION RECORD", [
            ("Full Name", name, "name", name),
            ("Birth Date", f"{dob[2]:02d}-{dob[1]:02d}-{dob[0]}", "date_of_birth", f"{dob[0]}-{dob[1]:02d}-{dob[2]:02d}"),
            ("Aadhaar No.", aadhaar, "aadhaar_number", aadhaar.replace(" ", "")),
        ], []
    if doc_type == "income_certificate":
        income = rng.randrange(80_000, 190_000, 500)
        return "INCOME DECLARATION (VERIFIED)", [
            ("Name of Applicant", name, "name", name),
            ("Total Annual Income", f"Rs. {hard.inr(income)}", "annual_income", income),
            ("Date of Issue", issued, "issue_date", issued_iso),
        ], []
    if doc_type == "residence_proof":
        state = rng.choice(list(hard.STATES))
        return "PROOF OF RESIDENCE", [
            ("Resident Name", name, "name", name),
            ("State of Residence", state, "residence_state", hard.STATES[state]),
            ("Date of Issue", issued, "issue_date", issued_iso),
        ], []
    if doc_type == "health_insurance":
        policy = f"SUR/{rng.randint(2025, 2026)}/{rng.randint(100000, 999999)}"
        cover = rng.choice([300000, 500000])
        end = (rng.randint(2027, 2028), rng.randint(1, 12), 28)
        return "HEALTH POLICY SCHEDULE", [
            ("Insured Person", name, "name", name),
            ("Insurance Company", "Demo Suraksha General Insurance Ltd.", "insurer", "Demo Suraksha General Insurance Ltd."),
            ("Policy No.", policy, "policy_number", policy),
            ("Policy Valid Till", f"{end[2]:02d}/{end[1]:02d}/{end[0]}", "expiry_date", f"{end[0]}-{end[1]:02d}-{end[2]:02d}"),
            ("Sum Assured", f"Rs. {hard.inr(cover)}", "sum_insured", cover),
        ], []
    if doc_type == "bank_passbook":
        account = "".join(str(rng.randint(0, 9)) for _ in range(12))
        ifsc = "DGBK0" + "".join(str(rng.randint(0, 9)) for _ in range(6))
        return "SAVINGS ACCOUNT DETAILS", [
            ("Account Holder", name, "name", name),
            ("Bank", "Demo Gramin Bank", "bank_name", "Demo Gramin Bank"),
            ("Account Number", account, "account_number", account),
            ("IFSC", ifsc, "ifsc", ifsc),
        ], []
    if doc_type == "disability_certificate":
        percent = rng.choice([40, 45, 60, 75])
        return "DISABILITY ASSESSMENT", [
            ("Name of Person", name, "name", name),
            ("Certifying Authority", "District Medical Board, Demo District", "issuing_authority",
             "District Medical Board, Demo District"),
            ("Date of Issue", issued, "issue_date", issued_iso),
            ("Percentage of Disability", f"{percent}%", "disability_percent", percent),
        ], []
    if doc_type == "lab_report":
        tests = rng.sample(hard.LAB_TESTS, 3)
        table = [("Test", "Result", "Units", "Normal Range")]
        rows = []
        for test, unit, ref, low, high in tests:
            result = round(rng.uniform(low, high), 1)
            table.append((test, str(result), unit, ref))
            rows.append({"name": test, "value": result, "unit": unit, "ref_range": ref})
        return "LABORATORY RESULTS", [
            ("Patient", name, "name", name),
            ("Laboratory Name", "Demo Path Labs", "lab_name", "Demo Path Labs"),
            ("Report Date", issued, "report_date", issued_iso),
            ("__table__", None, "tests", rows),
        ], table
    if doc_type == "prescription":
        meds = rng.sample(hard.MEDICINES, 2)
        table = [("Medicine", "Strength", "Dose", "Days")]
        rows = []
        for form, med, strength in meds:
            dose, days = rng.choice(["1-0-1", "1-0-0", "0-0-1"]), rng.choice([5, 10, 30])
            table.append((med, strength, dose, f"{days} days"))
            rows.append({"name": med, "form": None, "strength": strength, "frequency": dose,
                         "duration": f"{days} days"})
        return "OUTPATIENT PRESCRIPTION", [
            ("Patient", name, "name", name),
            ("Consulting Doctor", "Dr. Sunita Rao", "doctor_name", "Dr. Sunita Rao"),
            ("Date", issued, "prescription_date", issued_iso),
            ("__table__", None, "medicines", rows),
        ], table
    if doc_type == "medical_bill":
        items = rng.sample(hard.BILL_ITEMS, 2)
        table = [("Description", "Amount")]
        rows, total = [], 0.0
        for description, amount in items:
            table.append((description, f"{amount:.2f}"))
            rows.append({"description": description, "amount": amount})
            total += amount
        return "HOSPITAL BILL", [
            ("Patient", name, "name", name),
            ("Hospital", "Demo Care Hospital", "provider", "Demo Care Hospital"),
            ("Bill Date", issued, "bill_date", issued_iso),
            ("__table__", None, "items", rows),
            ("Net Payable", f"{total:.2f}", "total_amount", round(total, 2)),
        ], table
    raise ValueError(doc_type)


def make_form(doc_type: str, rng: random.Random, person: dict[str, Any]) -> tuple[Image.Image, dict, list]:
    title, entries, table = form_values(doc_type, rng, person)
    width = 1100
    height = 260 + 64 * len(entries) + 56 * len(table) + 140
    img, draw = hard.new_page(width, height, (255, 255, 255))
    draw.text((50, 40), title, font=hard.font("arialbd.ttf", 36), fill=hard.NAVY)
    draw.text((50, 92), "SAMPLE - fictional test form, not valid for any purpose", font=hard.font("arial.ttf", 22),
              fill=(160, 30, 30))
    y = 150
    fields: dict[str, Any] = {}
    truth_rows: list[list[str]] = []
    label_font, value_font = hard.font("arialbd.ttf", 26), hard.font("arial.ttf", 28)
    for label, shown, key, value in entries:
        if label == "__table__":
            col = [60, 420, 650, 860][: len(table[0])] if len(table[0]) == 4 else [60, 760]
            for r, cells in enumerate(table):
                ry = y + r * 56
                draw.rectangle((50, ry, width - 50, ry + 56), outline=(90, 90, 90), width=1)
                for x, cell in zip(col, cells):
                    draw.text((x, ry + 13), cell, font=label_font if r == 0 else value_font, fill=hard.INK)
                if r:
                    truth_rows.append(list(cells))
            first = table[1][0]
            fields[key] = {"shown": first, "value": value}
            y += 56 * len(table) + 20
            continue
        draw.rectangle((50, y, width - 50, y + 64), outline=(90, 90, 90), width=1)
        draw.line((400, y, 400, y + 64), fill=(90, 90, 90), width=1)
        draw.text((65, y + 17), label, font=label_font, fill=hard.INK)
        draw.text((420, y + 15), shown, font=value_font, fill=hard.INK)
        fields[key] = {"shown": shown, "value": value}
        y += 64
    draw.text((50, y + 40), "Verified by: Demo Office (fictional)", font=hard.font("arial.ttf", 22), fill=(90, 90, 90))
    return img, fields, truth_rows


# ---------- PDFs ----------

def text_pdf(lines: list[str]) -> bytes:
    """A one-page PDF whose text is stored in it (like a downloaded e-certificate)."""
    content = ["BT", "/F1 13 Tf", "16 TL", "60 780 Td"]
    for line in lines:
        safe = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").encode("latin-1", "replace").decode("latin-1")
        content.append(f"({safe}) Tj T*")
    content.append("ET")
    stream = "\n".join(content).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    return assemble_pdf(objects)


def assemble_pdf(objects: list[bytes]) -> bytes:
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


TEXT_PDF_TYPES = ["identity", "income_certificate", "health_insurance", "lab_report"]
SCANNED_PDF_TYPES = ["residence_proof", "bank_passbook"]


def text_pdf_lines(title: str, fields: dict[str, Any], table: list) -> list[str]:
    lines = [title, "SAMPLE - fictional digital certificate", ""]
    labels = {"name": "Name", "date_of_birth": "Date of Birth", "aadhaar_number": "Aadhaar Number",
              "annual_income": "Annual Income", "issue_date": "Issue Date", "insurer": "Insurer",
              "policy_number": "Policy Number", "expiry_date": "Expiry Date", "sum_insured": "Sum Insured",
              "lab_name": "Lab Name", "report_date": "Report Date"}
    for key, truth in fields.items():
        if key in labels:
            lines.append(f"{labels[key]}: {truth['shown']}")
    if table:
        lines.append("")
        lines.extend("   ".join(row) for row in table)
    return lines


# ---------- cases written by hand ----------

def privacy_cases(rng: random.Random) -> list[dict[str, Any]]:
    """mask: what mask_ids_in_text must hide / keep. redact: what an LLM prompt must not carry."""
    cases = []

    def aadhaar_digits() -> str:
        return fake_aadhaar(rng).replace(" ", "")

    for style in ("spaced", "dashed", "glued"):
        digits = aadhaar_digits()
        shown = {"spaced": f"{digits[:4]} {digits[4:8]} {digits[8:]}", "dashed": f"{digits[:4]}-{digits[4:8]}-{digits[8:]}",
                 "glued": digits}[style]
        cases.append({"id": f"aadhaar_{style}", "text": f"Applicant UID {shown} verified on 12/03/2026.",
                      "hide": [shown], "keep": ["12/03/2026"], "category": "aadhaar"})
    body = aadhaar_digits()[:11]
    wrong = body + str((int(verhoeff_check_digit(body)) + 1) % 10)
    cases.append({"id": "aadhaar_bad_checksum_order_no", "text": f"Order no. {wrong[:4]} {wrong[4:8]} {wrong[8:]} dispatched.",
                  "hide": [], "keep": [f"{wrong[:4]} {wrong[4:8]} {wrong[8:]}"], "category": "decoy"})
    pan = hard.fake_pan(rng)
    cases.append({"id": "pan_alone", "text": f"Permanent Account Number {pan} linked.", "hide": [pan], "keep": [],
                  "category": "pan"})
    cases.append({"id": "pan_lookalike_invalid_holder", "text": "Ref code ABCDX1234F for the parcel.", "hide": [],
                  "keep": ["ABCDX1234F"], "category": "decoy"})
    for label in ("A/c No.", "Account Number:", "a/c#", "Account No -"):
        account = "".join(str(rng.randint(0, 9)) for _ in range(rng.choice([11, 12, 14])))
        cases.append({"id": f"account_{label}", "text": f"Pension credited to {label} {account} today.",
                      "hide": [account], "keep": [], "category": "account"})
    bare = "".join(str(rng.randint(0, 9)) for _ in range(12))
    cases.append({"id": "account_without_label", "text": f"Savings {bare} (no label printed)", "hide": [bare], "keep": [],
                  "category": "account_unlabelled", "documented_gap": True})
    for label in ("Policy No.", "Policy Number:", "policy #"):
        policy = f"HLT/{rng.randint(2024, 2026)}/{rng.randint(100000, 999999)}"
        cases.append({"id": f"policy_{label}", "text": f"Cashless claim under {label} {policy} approved.",
                      "hide": [policy], "keep": [], "category": "policy"})
    for decoy in ("9876543210", "+91 98765 43210", "560001", "Rs. 1,45,000", "2026-10-27", "SBIN0001234"):
        cases.append({"id": f"keep_{decoy}", "text": f"Contact/amount/date: {decoy}.", "hide": [], "keep": [decoy],
                      "category": "decoy"})
    # Prompts for the model: IDs, phone numbers, e-mail and the known name must not be sent.
    name = PEOPLE[1]["name"]
    digits = aadhaar_digits()
    cases.append({"id": "redact_mixed", "kind": "redact", "known": {"NAME": name},
                  "text": f"Patient {name}, Aadhaar {digits[:4]} {digits[4:8]} {digits[8:]}, phone 9123456780, "
                          f"mail mohan.verma@example.com, bill Rs. 2,450.00 on 03/09/2026.",
                  "hide": [name, f"{digits[:4]} {digits[4:8]} {digits[8:]}", "9123456780", "mohan.verma@example.com"],
                  "keep": ["2,450.00", "03/09/2026"], "category": "redact"})
    return cases


MANUAL_CASES = [
    # (id, type, fields, accept, expected stored values or error words)
    ("identity_ok", "identity", {"name": "Kavita Sharma", "date_of_birth": "14/08/1952"}, True,
     {"name": "Kavita Sharma", "date_of_birth": "1952-08-14"}),
    ("identity_with_aadhaar", "identity", {"name": "Kavita Sharma", "date_of_birth": "1952-08-14",
                                          "aadhaar_number": "AADHAAR_VALID"}, True, {"aadhaar_number": "MASKED"}),
    ("identity_bad_aadhaar", "identity", {"name": "Kavita Sharma", "date_of_birth": "1952-08-14",
                                         "aadhaar_number": "AADHAAR_INVALID"}, False, ["aadhaar_number"]),
    ("identity_bad_pan", "identity", {"name": "Kavita Sharma", "date_of_birth": "1952-08-14", "pan_number": "ABCDE12345"},
     False, ["pan_number"]),
    ("identity_impossible_date", "identity", {"name": "Kavita Sharma", "date_of_birth": "31/02/1952"}, False,
     ["date_of_birth"]),
    ("identity_missing_dob", "identity", {"name": "Kavita Sharma"}, False, ["date_of_birth"]),
    ("identity_unknown_field", "identity", {"name": "Kavita Sharma", "date_of_birth": "1952-08-14", "blood_group": "B+"},
     False, ["blood_group"]),
    ("income_plain_number", "income_certificate", {"name": "Mohan Lal Verma", "annual_income": "145000",
                                                   "issue_date": "02/04/2026"}, True, {"annual_income": 145000}),
    ("income_indian_commas", "income_certificate", {"name": "Mohan Lal Verma", "annual_income": "1,45,000",
                                                    "issue_date": "02/04/2026"}, True, {"annual_income": 145000}),
    ("income_with_rupee_text", "income_certificate", {"name": "Mohan Lal Verma", "annual_income": "Rs. 1,45,000/-",
                                                      "issue_date": "02/04/2026"}, True, {"annual_income": 145000}),
    ("income_in_words", "income_certificate", {"name": "Mohan Lal Verma", "annual_income": "one lakh",
                                               "issue_date": "02/04/2026"}, False, ["annual_income"]),
    ("income_future_issue", "income_certificate", {"name": "Mohan Lal Verma", "annual_income": "145000",
                                                   "issue_date": "02/04/2027"}, False, ["future"]),
    ("residence_state_name", "residence_proof", {"name": "Farida Begum", "residence_state": "Maharashtra",
                                                 "issue_date": "2026-01-15"}, True, {"residence_state": "MH"}),
    ("residence_state_lowercase", "residence_proof", {"name": "Farida Begum", "residence_state": "karnataka",
                                                      "issue_date": "2026-01-15"}, True, {"residence_state": "KA"}),
    ("residence_state_code", "residence_proof", {"name": "Farida Begum", "residence_state": "TN",
                                                 "issue_date": "2026-01-15"}, True, {"residence_state": "TN"}),
    ("residence_not_a_state", "residence_proof", {"name": "Farida Begum", "residence_state": "Bombay",
                                                  "issue_date": "2026-01-15"}, False, ["residence_state"]),
    ("insurance_ok", "health_insurance", {"name": "Joseph D'Souza", "insurer": "Demo Health Insurance Co. Ltd.",
                                          "policy_number": "HLT/2026/556677", "expiry_date": "31-03-2028",
                                          "sum_insured": "5,00,000"}, True,
     {"policy_number": "MASKED", "sum_insured": 500000, "expiry_date": "2028-03-31"}),
    ("insurance_policy_no_digits", "health_insurance", {"name": "Joseph D'Souza", "insurer": "Demo Health Insurance",
                                                        "policy_number": "ABCDEFG", "expiry_date": "2028-03-31",
                                                        "sum_insured": "500000"}, False, ["policy_number"]),
    ("passbook_ok", "bank_passbook", {"name": "Kavita Sharma", "bank_name": "Demo Gramin Bank",
                                      "account_number": "123456789012", "ifsc": "dgbk0123456"}, True,
     {"account_number": "MASKED", "ifsc": "DGBK0123456"}),
    ("passbook_short_account", "bank_passbook", {"name": "Kavita Sharma", "bank_name": "Demo Gramin Bank",
                                                 "account_number": "12345", "ifsc": "DGBK0123456"}, False,
     ["account_number"]),
    ("passbook_bad_ifsc", "bank_passbook", {"name": "Kavita Sharma", "bank_name": "Demo Gramin Bank",
                                            "account_number": "123456789012", "ifsc": "DGBK1123456"}, False, ["ifsc"]),
    ("disability_percent_sign", "disability_certificate", {"name": "Mohan Lal Verma", "issuing_authority": "District Medical Board",
                                                           "issue_date": "2026-05-05", "disability_percent": "45 %"}, True,
     {"disability_percent": 45}),
    ("disability_over_100", "disability_certificate", {"name": "Mohan Lal Verma", "issuing_authority": "District Medical Board",
                                                       "issue_date": "2026-05-05", "disability_percent": "120"}, False,
     ["disability_percent"]),
    ("lab_rows", "lab_report", {"name": "Farida Begum", "lab_name": "Demo Path Labs", "report_date": "2026-09-09",
                                "tests": [{"name": "HbA1c", "value": "6.4", "unit": "%", "ref_range": "4.0 - 5.6"}]},
     True, {"tests": [{"name": "HbA1c", "value": 6.4, "flag": "high"}]}),
    ("lab_row_without_name", "lab_report", {"name": "Farida Begum", "lab_name": "Demo Path Labs", "report_date": "2026-09-09",
                                            "tests": [{"value": "6.4"}]}, False, ["tests row 1"]),
    ("prescription_rows", "prescription", {"name": "Joseph D'Souza", "doctor_name": "Dr. Sunita Rao",
                                           "prescription_date": "2026-10-01",
                                           "medicines": [{"name": "Metformin", "strength": "500 mg", "frequency": "1-0-1"}]},
     True, {"medicines": [{"name": "Metformin", "strength": "500 mg", "frequency": "1-0-1"}]}),
    ("prescription_medicines_as_text", "prescription", {"name": "Joseph D'Souza", "doctor_name": "Dr. Sunita Rao",
                                                        "prescription_date": "2026-10-01", "medicines": "Metformin 500"},
     False, ["medicines"]),
    ("bill_rupee_amount", "medical_bill", {"name": "Kavita Sharma", "provider": "Demo Care Hospital", "bill_date": "2026-10-10",
                                           "total_amount": "₹ 1,250.50"}, True, {"total_amount": 1250.5}),
    ("bill_item_amount_unreadable", "medical_bill", {"name": "Kavita Sharma", "provider": "Demo Care Hospital",
                                                     "bill_date": "2026-10-10", "total_amount": "100",
                                                     "items": [{"description": "Dressing", "amount": "abc"}]},
     False, ["items row 1"]),
    ("unknown_type", "passport", {"name": "Kavita Sharma"}, False, ["Choose one of"]),
    ("nothing_typed", "identity", {}, False, ["required"]),
]


def benefit_document(doc_id: str, doc_type: str, fields: dict[str, Any], *, owner: str = "senior-applicant",
                     status: str = "confirmed", expiry: str | None = None) -> dict[str, Any]:
    return {"document_id": doc_id, "owner_id": owner, "type": doc_type, "status": status, "expiry_date": expiry,
            "confirmed_fields": {key: {"value": value, "source_ref": f"document:{doc_id}:entered_by_owner"}
                                 for key, value in fields.items()}}


def benefits_cases() -> list[dict[str, Any]]:
    """Each case: a state for the benefits graph and the outcome(s) a correct system gives.
    Rules (fictional scheme): age >= 60, annual income <= 2,00,000, state MH; needs identity,
    income certificate and residence proof, all confirmed, the owner's own and not expired."""
    def good(**change):
        docs = {
            "identity": benefit_document("id-1", "identity", {"name": "Kavita Sharma", "date_of_birth": "1952-08-14"}),
            "income": benefit_document("inc-1", "income_certificate", {"name": "Kavita Sharma", "annual_income": 145000,
                                                                        "issue_date": "2026-04-02"}),
            "residence": benefit_document("res-1", "residence_proof", {"name": "Kavita Sharma", "residence_state": "MH",
                                                                        "issue_date": "2026-01-15"}),
        }
        docs.update(change.pop("docs", {}))
        state = {"actor_id": "senior-applicant", "case_id": "case-1", "case_version": 1, "expected_case_version": 1,
                 "scheme_id": "demo_senior_support", "jurisdiction": "MH", "as_of": AS_OF, "profile_facts": {},
                 "need_text": "", "documents": [d for d in docs.values() if d is not None],
                 "review_decision": {"action": "approve", "reviewer_id": "senior-applicant"}}
        state.update(change)
        return state

    def income(value, **kw):
        return benefit_document("inc-x", "income_certificate", {"name": "Kavita Sharma", "annual_income": value,
                                                                "issue_date": "2026-04-02"}, **kw)

    def identity(dob, name="Kavita Sharma"):
        return benefit_document("id-x", "identity", {"name": name, "date_of_birth": dob})

    cases = [
        ("all_met_and_approved", good(), ["ready_for_manual_handoff"]),
        ("all_met_no_decision_yet", good(review_decision=None), ["awaiting_review"]),
        ("income_exactly_limit", good(docs={"income": income(200000)}), ["ready_for_manual_handoff"]),
        ("income_one_over_limit", good(docs={"income": income(200001)}), ["not_eligible_in_demo"]),
        ("turns_60_today", good(docs={"identity": identity("1966-10-27")}), ["ready_for_manual_handoff"]),
        ("turns_60_tomorrow", good(docs={"identity": identity("1966-10-28")}), ["not_eligible_in_demo"]),
        ("lives_in_other_state", good(docs={"residence": benefit_document("res-x", "residence_proof", {
            "name": "Kavita Sharma", "residence_state": "KA", "issue_date": "2026-01-15"})}), ["not_eligible_in_demo"]),
        ("income_certificate_missing", good(docs={"income": None}), ["needs_input"]),
        ("income_certificate_expired", good(docs={"income": income(145000, expiry="2026-06-30")}), ["needs_input"]),
        ("income_certificate_unconfirmed", good(docs={"income": income(145000, status="needs_review")}), ["needs_input"]),
        ("income_certificate_of_someone_else", good(docs={"income": income(145000, owner="senior-other")}), ["needs_input"]),
        ("two_incomes_disagree", good(docs={"income2": benefit_document("inc-2", "income_certificate", {
            "name": "Kavita Sharma", "annual_income": 150000, "issue_date": "2026-05-02"})}), ["blocked_conflict"]),
        ("names_differ", good(docs={"identity": identity("1952-08-14", name="Kavita Sharmaa")}), ["blocked_conflict"]),
        ("invalid_birth_date", good(docs={"identity": identity("1952-02-30")}), ["blocked_conflict"]),
        ("profile_age_contradicts_birth_date", good(profile_facts={"age": {"value": 80, "confirmed": True,
                                                                          "source_ref": "profile:age"}}),
         ["blocked_conflict"]),
        ("scheme_from_need_text", good(scheme_id=None, need_text="I want to apply for an old age pension"),
         ["ready_for_manual_handoff"]),
        ("need_text_unclear", good(scheme_id=None, need_text="help with my rent"), ["needs_scheme_choice"]),
        ("scheme_not_for_this_state", good(jurisdiction="KA"), ["needs_scheme_choice"]),
        ("case_changed_meanwhile", good(case_version=2), ["blocked"]),
        ("scheme_source_unavailable", good(simulate_source_unavailable=True), ["blocked_source_review"]),
        ("owner_rejects", good(review_decision={"action": "reject", "reviewer_id": "senior-applicant"}), ["rejected"]),
        ("someone_else_approves", good(review_decision={"action": "approve", "reviewer_id": "senior-other"}),
         ["rejected", "awaiting_review"]),
        ("bad_as_of_date", good(as_of="27/10/2026"), ["blocked"]),
    ]
    return [{"id": name, "state": state, "expect": expect} for name, state, expect in cases]


def proof_cases() -> list[dict[str, Any]]:
    owner = "senior-applicant"
    identity = benefit_document("id-1", "identity", {"name": "Kavita Sharma", "date_of_birth": "1952-08-14"})
    old_income = benefit_document("inc-old", "income_certificate", {"name": "Kavita Sharma", "annual_income": 1,
                                                                    "issue_date": "2024-01-01"}, expiry="2025-01-01")
    new_income = benefit_document("inc-new", "income_certificate", {"name": "Kavita Sharma", "annual_income": 145000,
                                                                    "issue_date": "2026-04-02"})
    half_income = benefit_document("inc-half", "income_certificate", {"name": "Kavita Sharma", "annual_income": 145000})
    policy = benefit_document("pol-1", "health_insurance", {"name": "Kavita Sharma", "insurer": "Demo", "policy_number":
                                                            "XXXX5566", "expiry_date": "2025-12-31", "sum_insured": 1},
                              expiry="2025-12-31")
    return [dict(case, owner=owner) for case in [
        {"id": "age_from_identity", "proofs": ["proof_of_age"], "documents": [identity],
         "expect": [["usable", "id-1"]]},
        {"id": "newest_usable_income_wins", "proofs": ["proof_of_income"], "documents": [old_income, new_income],
         "expect": [["usable", "inc-new"]]},
        {"id": "only_expired_income", "proofs": ["proof_of_income"], "documents": [old_income],
         "expect": [["expired", "inc-old"]]},
        {"id": "income_missing_issue_date", "proofs": ["proof_of_income"], "documents": [half_income],
         "expect": [["unconfirmed", "inc-half"]]},
        {"id": "nothing_for_residence", "proofs": ["proof_of_residence"], "documents": [identity],
         "expect": [["missing", None]]},
        {"id": "expired_health_cover", "proofs": ["health_cover"], "documents": [policy],
         "expect": [["expired", "pol-1"]]},
        {"id": "someone_elses_identity", "proofs": ["proof_of_identity"],
         "documents": [benefit_document("id-o", "identity", {"name": "X", "date_of_birth": "1950-01-01"}, owner="other")],
         "expect": [["missing", None]]},
    ]]


# ---------- unsafe files ----------

def png_bytes(width: int, height: int) -> bytes:
    """A real PNG of any declared size; pixel data is tiny for a 1x1, made-up for big sizes."""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = zlib.compress(b"\x00" + b"\xff\xff\xff" * min(width, 4))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")


def risky_pdf(extra_catalog: bytes = b"", extra_objects: list[bytes] | None = None, pages: int = 1,
              media_box: bytes = b"[0 0 595 842]") -> bytes:
    kids = " ".join(f"{4 + i} 0 R" for i in range(pages)).encode()
    objects = [b"<< /Type /Catalog /Pages 2 0 R " + extra_catalog + b" >>",
               b"<< /Type /Pages /Kids [" + kids + b"] /Count " + str(pages).encode() + b" >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    for _ in range(pages):
        objects.append(b"<< /Type /Page /Parent 2 0 R /MediaBox " + media_box + b" >>")
    objects.extend(extra_objects or [])
    return assemble_pdf(objects)


def compressed_js_pdf() -> bytes:
    hidden = zlib.compress(b"<< /S /JavaScript /JS (app.alert('x')) >>")
    stream = b"<< /Length " + str(len(hidden)).encode() + b" /Filter /FlateDecode >>\nstream\n" + hidden + b"\nendstream"
    return risky_pdf(extra_objects=[stream])


def file_safety_cases(out: Path, clean_png: bytes, clean_jpg: bytes, clean_pdf: bytes, scanned_pdf: bytes) -> list[dict]:
    folder = out / "file_safety"
    folder.mkdir(parents=True, exist_ok=True)
    cases = [
        ("clean_photo.png", "image/png", clean_png, "accept"),
        ("clean_photo.jpg", "image/jpeg", clean_jpg, "accept"),
        ("clean_text.pdf", "application/pdf", clean_pdf, "accept"),
        ("clean_scanned.pdf", "application/pdf", scanned_pdf, "accept"),
        ("png_sent_as_pdf.pdf", "application/pdf", clean_png, "refuse"),
        ("program_sent_as_png.png", "image/png", b"MZ\x90\x00\x03" + b"\x00" * 200, "refuse"),
        ("web_page_sent_as_jpeg.jpg", "image/jpeg", b"<html><script>alert(1)</script></html>", "refuse"),
        ("empty.png", "image/png", b"", "refuse"),
        ("pdf_with_javascript.pdf", "application/pdf",
         risky_pdf(b"/OpenAction << /S /JavaScript /JS (app.alert('x')) >>"), "refuse"),
        ("pdf_launches_program.pdf", "application/pdf",
         risky_pdf(b"/OpenAction << /S /Launch /F (cmd.exe) >>"), "refuse"),
        ("pdf_with_attached_file.pdf", "application/pdf",
         risky_pdf(b"/Names << /EmbeddedFiles << /Names [(a.exe) 4 0 R] >> >>",
                   [b"<< /Type /Filespec /F (a.exe) /EF << /F 5 0 R >> >>"]), "refuse"),
        ("pdf_hidden_javascript_name.pdf", "application/pdf",
         risky_pdf(b"/OpenAction << /S /J#61vaScript /JS (x) >>"), "refuse"),
        ("pdf_javascript_in_compressed_stream.pdf", "application/pdf", compressed_js_pdf(), "refuse"),
        ("pdf_submits_form.pdf", "application/pdf", risky_pdf(b"/OpenAction << /S /SubmitForm /F (http://x) >>"),
         "refuse"),
        ("pdf_21_pages.pdf", "application/pdf", risky_pdf(pages=21), "refuse"),
        ("pdf_huge_page.pdf", "application/pdf", risky_pdf(media_box=b"[0 0 14400 14400]"), "refuse"),
        ("png_pixel_bomb.png", "image/png", png_bytes(40000, 40000), "refuse"),
        ("png_cut_off.png", "image/png", clean_png[: len(clean_png) // 3], "refuse"),
        ("pdf_cut_off.pdf", "application/pdf", clean_pdf[:120], "refuse"),
    ]
    manifest = []
    for name, mime, data, verdict in cases:
        (folder / name).write_bytes(data)
        manifest.append({"file": f"file_safety/{name}", "mime_type": mime, "expect": verdict})
    return manifest


# ---------- generate ----------

def save_page(arr_or_img, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = arr_or_img if isinstance(arr_or_img, Image.Image) else Image.fromarray(arr_or_img)
    image.convert("RGB").save(path)


def generate(out: Path = OUT) -> dict[str, Any]:
    rng = random.Random(SEED)
    pages: list[dict[str, Any]] = []
    for doc in seen_documents():
        conditions = list(SEEN_CONDITIONS)
        if doc["pack"] in (None, "eligible"):  # one worn copy of each of the 9 types
            conditions.append(WORN_ONCE)
        for condition in conditions:
            arr = hard.apply_condition(doc["image"], condition, random.Random(f"{SEED}:{doc['id']}:{condition}"))
            name = f"documents/seen/{doc['id']}__{condition}.png"
            save_page(arr, out / name)
            pages.append({"path": name, "mime_type": "image/png", "type": doc["type"], "layout": "seen",
                          "condition": condition, "document": doc["id"], "person": doc["person"], "pack": doc["pack"],
                          "fields": doc["fields"], "rows": doc["rows"]})
    form_sources: dict[str, Any] = {}
    for index, doc_type in enumerate(hard.TYPES):
        person = PEOPLE[index % len(PEOPLE)]
        img, fields, rows = make_form(doc_type, random.Random(f"{SEED}:form:{doc_type}"), person)
        form_sources[doc_type] = (img, fields, rows, person)
        for condition in ("clean", "phone_photo"):
            arr = img if condition == "clean" else hard.apply_condition(img, condition,
                                                                          random.Random(f"{SEED}:form:{doc_type}:{condition}"))
            name = f"documents/unseen/{doc_type}__{condition}.png"
            save_page(arr, out / name)
            pages.append({"path": name, "mime_type": "image/png", "type": doc_type, "layout": "unseen",
                          "condition": condition, "document": f"form__{doc_type}", "person": person["name"],
                          "pack": None, "fields": fields, "rows": rows})
    for doc_type in TEXT_PDF_TYPES:
        img, fields, rows, person = form_sources[doc_type]
        title, _, table = form_values(doc_type, random.Random(f"{SEED}:form:{doc_type}"), person)
        name = f"documents/pdf/{doc_type}__text.pdf"
        (out / name).parent.mkdir(parents=True, exist_ok=True)
        (out / name).write_bytes(text_pdf(text_pdf_lines(title, fields, table)))
        pages.append({"path": name, "mime_type": "application/pdf", "type": doc_type, "layout": "text_pdf",
                      "condition": "digital", "document": f"form__{doc_type}", "person": person["name"], "pack": None,
                      "fields": fields, "rows": rows})
    for doc_type in SCANNED_PDF_TYPES:
        img, fields, rows, person = form_sources[doc_type]
        arr = hard.apply_condition(img, "scan", random.Random(f"{SEED}:pdf:{doc_type}"))
        name = f"documents/pdf/{doc_type}__scanned.pdf"
        Image.fromarray(arr).convert("RGB").save(out / name, "PDF", resolution=150)
        pages.append({"path": name, "mime_type": "application/pdf", "type": doc_type, "layout": "scanned_pdf",
                      "condition": "scan", "document": f"form__{doc_type}", "person": person["name"], "pack": None,
                      "fields": fields, "rows": rows})
    (out / "documents" / "manifest.json").write_text(json.dumps(pages, indent=1, ensure_ascii=False), encoding="utf-8")

    for folder, data in (("privacy", privacy_cases(rng)), ("benefits", benefits_cases()), ("proofs", proof_cases())):
        (out / folder).mkdir(parents=True, exist_ok=True)
        (out / folder / "cases.json").write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")

    manual = []
    for case_id, doc_type, fields, accept, expect in MANUAL_CASES:
        fields = dict(fields)
        if fields.get("aadhaar_number") == "AADHAAR_VALID":
            fields["aadhaar_number"] = fake_aadhaar(rng)
        elif fields.get("aadhaar_number") == "AADHAAR_INVALID":
            good = fake_aadhaar(rng).replace(" ", "")
            fields["aadhaar_number"] = good[:11] + str((int(good[11]) + 1) % 10)
        manual.append({"id": case_id, "type": doc_type, "fields": fields, "accept": accept, "expect": expect})
    (out / "manual_entry").mkdir(parents=True, exist_ok=True)
    (out / "manual_entry" / "cases.json").write_text(json.dumps(manual, indent=1, ensure_ascii=False), encoding="utf-8")

    clean_png = (out / pages[0]["path"]).read_bytes()
    jpg_path = out / "file_safety" / "_tmp.jpg"
    jpg_path.parent.mkdir(parents=True, exist_ok=True)
    Image.open(out / pages[0]["path"]).convert("RGB").save(jpg_path, quality=85)
    clean_jpg = jpg_path.read_bytes()
    jpg_path.unlink()
    safety = file_safety_cases(out, clean_png, clean_jpg,
                               (out / "documents/pdf/income_certificate__text.pdf").read_bytes(),
                               (out / "documents/pdf/residence_proof__scanned.pdf").read_bytes())
    (out / "file_safety" / "manifest.json").write_text(json.dumps(safety, indent=1), encoding="utf-8")
    summary = {"seed": SEED, "as_of": AS_OF, "pages": len(pages),
               "by_layout": {k: sum(p["layout"] == k for p in pages) for k in ("seen", "unseen", "text_pdf", "scanned_pdf")},
               "privacy_cases": len(privacy_cases(random.Random(SEED))), "manual_cases": len(manual),
               "benefits_cases": len(benefits_cases()), "proof_cases": len(proof_cases()), "file_cases": len(safety)}
    (out / "dataset_summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return summary


if __name__ == "__main__":
    print(json.dumps(generate(), indent=1))
