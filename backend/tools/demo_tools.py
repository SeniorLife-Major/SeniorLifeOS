"""Small local tool adapters. Replace these with OCR/catalogue services later.

The benefit scheme is fictional. Its criteria are useful for exercising graph
branches, not advice about an actual Indian government programme.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from ..utils.privacy import AADHAAR_PATTERN, is_valid_aadhaar, is_valid_ifsc, is_valid_pan
from .layout import STOP, label_above, no_colon_pair, pairs_in_line
from .medical import LIST_FIELDS, normalize_rows, parse_money

DATE_FIELDS = {"issue_date", "expiry_date", "date_of_birth", "report_date", "prescription_date", "bill_date"}


DEMO_SCHEMES: dict[str, dict[str, Any]] = {
    "demo_senior_support": {
        "scheme_id": "demo_senior_support",
        "name": "Demo Senior Support Pension (fictional)",
        "jurisdiction": "MH",
        "source": {
            "source_id": "demo-source-v1",
            "publisher": "SeniorLife OS fictional demo catalogue",
            "url": "demo://schemes/demo_senior_support/v1",
            "retrieved_at": "2026-09-22",
            "version": "v1",
            "approved_for_demo": True,
        },
        "criteria": [
            {"id": "age", "fact": "age", "operator": ">=", "value": 60},
            {"id": "annual_income", "fact": "annual_income", "operator": "<=", "value": 200000},
            {"id": "residence_state", "fact": "residence_state", "operator": "==", "value": "MH"},
        ],
        "required_documents": ["identity", "income_certificate", "residence_proof"],
    }
}


def get_scheme(scheme_id: str) -> dict[str, Any] | None:
    """Simulate a narrow, allowlisted scheme catalogue lookup."""
    return DEMO_SCHEMES.get(scheme_id)


FIELD_LABELS = {
    "name": "name",
    "member name": "name",
    "patient name": "name",
    "date of birth": "date_of_birth",
    "dob": "date_of_birth",
    "annual income": "annual_income",
    "state": "residence_state",
    "issue date": "issue_date",
    "expiry date": "expiry_date",
    "issuer": "issuer",
    "aadhaar": "aadhaar_number",
    "aadhaar no": "aadhaar_number",
    "pan": "pan_number",
    "pan no": "pan_number",
    "account number": "account_number",
    "account no": "account_number",
    "a/c no": "account_number",
    "insurer": "insurer",
    "policy no": "policy_number",
    "policy number": "policy_number",
    "valid till": "expiry_date",
    "valid upto": "expiry_date",
    "valid up to": "expiry_date",
    "sum insured": "sum_insured",
    "bank": "bank_name",
    "bank name": "bank_name",
    "ifsc": "ifsc",
    "ifsc code": "ifsc",
    "issuing authority": "issuing_authority",
    "issued by": "issuing_authority",
    "disability": "disability_percent",
    "disability percentage": "disability_percent",
}
# Labels whose meaning depends on the document: a bare "Date:" is the date of issue on a
# certificate but the bill date on a bill; "Total" is only a field on a bill.
CERTIFICATE_TYPES = {"income_certificate", "residence_proof", "disability_certificate"}
CERTIFICATE_LABELS = {"date": "issue_date", "dated": "issue_date"}
TYPE_LABELS = {
    **{doc_type: CERTIFICATE_LABELS for doc_type in CERTIFICATE_TYPES},
    "lab_report": {"reported on": "report_date", "report date": "report_date", "lab name": "lab_name",
                   "laboratory": "lab_name"},
    "prescription": {"date": "prescription_date", "doctor": "doctor_name", "doctor name": "doctor_name",
                     "consultant": "doctor_name", "patient": "name"},
    "medical_bill": {"date": "bill_date", "bill date": "bill_date", "invoice date": "bill_date",
                     "total": "total_amount", "grand total": "total_amount", "net amount": "total_amount",
                     "amount payable": "total_amount", "net payable": "total_amount",
                     "total amount": "total_amount", "bill amount": "total_amount",
                     "hospital name": "provider", "pharmacy name": "provider", "patient": "name"},
}
# Labels that are not fields here but end the value before them ("... Age / Sex: 69").
STOP_LABELS = {
    "age", "sex", "gender", "age/sex", "place", "date", "ref by", "sample id", "collected on", "reported on",
    "member id", "valid from", "cif no", "date of opening", "nominee", "branch", "no", "toll free",
    "address", "mobile", "phone", "email", "father's name", "husband's name", "s/o", "w/o", "d/o",
}


def compact_label(label: str) -> str:
    """'Policy No.', 'PolicyNo', 'IFSC Code .........' -> 'policyno', 'policyno', 'ifsccode'."""
    return re.sub(r"[\s.]", "", label.lower())


# OCR often drops the space inside a label or prints dotted leaders after it.
COMPACT_FIELD_LABELS = {compact_label(label): key for label, key in FIELD_LABELS.items()}
COMPACT_TYPE_LABELS = {doc_type: {compact_label(label): key for label, key in labels.items()}
                       for doc_type, labels in TYPE_LABELS.items()}
COMPACT_STOP_LABELS = {compact_label(label) for label in STOP_LABELS}


def field_for_label(raw_label: str, document_type: str | None = None) -> str | None:
    """The field a printed label stands for, ignoring case, spaces and dots.

    A bilingual label keeps its English part: 'जन्म तिथि / DOB' (often OCR'd as 'H/DOB') -> DOB.
    """
    labels = dict(COMPACT_FIELD_LABELS)
    labels.update(COMPACT_TYPE_LABELS.get(document_type or "", {}))
    key = FIELD_LABELS.get(raw_label.strip().lower()) or labels.get(compact_label(raw_label))
    if key is None and "/" in raw_label:
        key = labels.get(compact_label(raw_label.rsplit("/", 1)[1]))
    return key


def label_kind(raw_label: str, document_type: str | None = None) -> str | None:
    """A field key, layout.STOP for a known non-field label, or None."""
    key = field_for_label(raw_label, document_type)
    if key:
        return key
    return STOP if compact_label(raw_label) in COMPACT_STOP_LABELS else None


def extract_fields_from_pages(pages: list[dict[str, Any]], document_type: str | None = None) -> dict[str, dict[str, Any]]:
    """Find fields in page text: "Label: value" (several per line allowed), then layout rules.

    The pages are either handwritten demo fixtures or the output of real OCR
    (tools/ocr.py). Each field carries a page and line reference. Fields found only by
    a layout rule (no colon, label above the value) carry "layout" so they get reviewed;
    they never replace a field found with a colon.
    """
    lookup = lambda label: label_kind(label, document_type)  # noqa: E731
    found: dict[str, dict[str, Any]] = {}
    for page in pages:
        text_lines = page.get("text", "").splitlines()
        # Pages from real OCR (tools/ocr.py) carry one entry per text line with its own
        # confidence and box. Only trust them if they line up with the text.
        ocr_lines = page.get("lines")
        if not ocr_lines or len(ocr_lines) != len(text_lines):
            ocr_lines = None

        def item(value: str, line_number: int, part: dict[str, Any] | None = None, layout: str | None = None):
            confidence = page.get("quality", 1.0)
            entry = {
                "value": value,
                "page": page["page"],
                "line": line_number,
                "source_ref": f"page:{page['page']}:line:{line_number}",
            }
            if ocr_lines:
                ocr_line = part or ocr_lines[line_number - 1]
                # The line's (or piece's) own confidence; a blurry page keeps its lower cap.
                confidence = ocr_line.get("confidence", confidence)
                if page.get("blurry"):
                    confidence = min(confidence, page.get("quality", 1.0))
                entry["box"] = ocr_line.get("box")
            entry["confidence"] = confidence
            if layout:
                entry["layout"] = layout
            return entry

        for line_number, line in enumerate(text_lines, start=1):
            for key, value in pairs_in_line(line, lookup):
                found[key] = item(value, line_number)
        layout_found: dict[str, dict[str, Any]] = {}
        lines = ocr_lines or [{"text": text} for text in text_lines]
        for index, line in enumerate(text_lines):
            pair = no_colon_pair(line, lookup)
            if pair:
                layout_found.setdefault(pair[0], item(pair[1], index + 1, layout="no_colon"))
            if index + 1 < len(text_lines):
                for key, part in label_above(lines[index], lines[index + 1], lookup):
                    is_piece = "parts" in lines[index + 1] and part is not lines[index + 1]
                    layout_found.setdefault(key, item(part["text"], index + 2, part if is_piece else None,
                                                      layout="label_above"))
        for key, entry in layout_found.items():
            found.setdefault(key, entry)
        if document_type == "identity" and "aadhaar_number" not in found:
            # ID cards often print the number with no label. The check digit makes a wrong
            # 12-digit number very unlikely to pass; two different valid numbers are not guessed.
            hits = [(number, match.group(0)) for number, line in enumerate(text_lines, start=1)
                    for match in AADHAAR_PATTERN.finditer(line) if is_valid_aadhaar(match.group(0))]
            if len({re.sub(r"\D", "", text) for _, text in hits}) == 1:
                found["aadhaar_number"] = item(hits[0][1], hits[0][0], layout="checksum")
    return found


# States and union territories -> ISO 3166-2:IN code. Names are compared lower-case
# with "&" read as "and". Extra accepted codes map old or common codes to the ISO one.
INDIAN_STATES = {
    "andhra pradesh": "AP", "arunachal pradesh": "AR", "assam": "AS", "bihar": "BR", "chhattisgarh": "CT",
    "goa": "GA", "gujarat": "GJ", "haryana": "HR", "himachal pradesh": "HP", "jharkhand": "JH",
    "karnataka": "KA", "kerala": "KL", "madhya pradesh": "MP", "maharashtra": "MH", "manipur": "MN",
    "meghalaya": "ML", "mizoram": "MZ", "nagaland": "NL", "odisha": "OD", "orissa": "OD", "punjab": "PB",
    "rajasthan": "RJ", "sikkim": "SK", "tamil nadu": "TN", "telangana": "TG", "tripura": "TR",
    "uttarakhand": "UK", "uttar pradesh": "UP", "west bengal": "WB",
    "andaman and nicobar islands": "AN", "chandigarh": "CH",
    "dadra and nagar haveli and daman and diu": "DH", "delhi": "DL", "nct of delhi": "DL",
    "national capital territory of delhi": "DL", "jammu and kashmir": "JK", "ladakh": "LA",
    "lakshadweep": "LD", "puducherry": "PY", "pondicherry": "PY",
}
STATE_CODE_ALIASES = {"CG": "CT", "OR": "OD", "TS": "TG", "UA": "UK"}
STATE_CODES = set(INDIAN_STATES.values())
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september",
          "october", "november", "december"]
MONTH_NUMBERS = {**{name: n for n, name in enumerate(MONTHS, 1)},
                 **{name[:3]: n for n, name in enumerate(MONTHS, 1)}, "sept": 9}
# Dates are read day first, as on Indian documents.
ISO_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")                            # 2026-12-23
NUMERIC_DATE = re.compile(r"(\d{1,2})([/.-])(\d{1,2})\2(\d{4})")             # 23/12/2026 23-12-2026 23.12.2026
NAMED_MONTH_DATE = re.compile(r"(\d{1,2})[-\s]?([A-Za-z]{3,9})\.?[-\s,]*(\d{4})")  # 28-Aug-2029, 28 August 2029
NAME_TITLE = re.compile(r"^(?:smt|shrimati|shri|sri|mr|mrs|ms|miss|dr|kum|km)\.?\s+", re.IGNORECASE)
INDIAN_GROUPS = re.compile(r"\d{1,2}(,\d{2})*,\d{3}")  # 2,56,000 and 1,00,00,000
WESTERN_GROUPS = re.compile(r"\d{1,3}(,\d{3})+")       # 256,000


def _real_date(year: int, month: int | None, day: int) -> str | None:
    try:
        return date(year, month, day).isoformat() if month else None
    except ValueError:  # 31/02/2026 and the like
        return None


def parse_date(raw_value: str) -> str | None:
    """ISO date from '2026-12-23', '23/12/2026', '23.12.2026' or '28-Aug-2029'.

    Year-first with slashes (2026/06/01) and two-digit years are refused, not guessed.
    """
    value = raw_value.strip()
    if match := ISO_DATE.fullmatch(value):
        return _real_date(int(match[1]), int(match[2]), int(match[3]))
    if match := NUMERIC_DATE.fullmatch(value):
        return _real_date(int(match[4]), int(match[3]), int(match[1]))
    if match := NAMED_MONTH_DATE.fullmatch(value):
        return _real_date(int(match[3]), MONTH_NUMBERS.get(match[2].lower()), int(match[1]))
    return None


def parse_amount(raw_value: str) -> int | None:
    """Rupee amount -> whole rupees: '₹2,56,000/-', 'Rs. 2,56,000', '256000.00'.

    Commas must sit in Indian or Western positions, so '2.56.000' (an OCR misread)
    is refused instead of guessed. Paise other than .00 are refused.
    """
    value = raw_value.strip()
    value = re.sub(r"^(?:₹|Rs\.?|INR)\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s*(?:/-|/=)$", "", value)
    value = re.sub(r"\.0{1,2}$", "", value)
    if re.fullmatch(r"\d+", value) or INDIAN_GROUPS.fullmatch(value) or WESTERN_GROUPS.fullmatch(value):
        return int(value.replace(",", ""))
    return None


def parse_state(raw_value: str) -> str | None:
    """State or union territory name/code -> ISO code ('Kerala' -> 'KL', 'MH' -> 'MH')."""
    value = raw_value.strip().rstrip(".")
    code = value.upper()
    if code in STATE_CODES:
        return code
    if code in STATE_CODE_ALIASES:
        return STATE_CODE_ALIASES[code]
    name = " ".join(value.lower().replace("&", " and ").split())
    name = re.sub(r"^(?:the\s+)?(?:state|union territory|u\.?t\.?)\s+of\s+", "", name)
    return INDIAN_STATES.get(name)


def normalize_field(key: str, raw_value: Any) -> Any:
    """Normalize known fields without guessing unclear values."""
    if key in LIST_FIELDS:
        return normalize_rows(key, raw_value)  # lab tests, medicines, bill items
    if key in {"annual_income", "sum_insured"}:
        return parse_amount(raw_value)
    if key == "total_amount":
        return parse_money(raw_value)  # bills can have paise
    if key in DATE_FIELDS:
        return parse_date(raw_value)
    if key == "residence_state":
        return parse_state(raw_value)
    if key == "name":
        # "Smt. Anita Deshpande" -> "Anita Deshpande": a title is not part of the name.
        return NAME_TITLE.sub("", " ".join(raw_value.split()))
    if key == "aadhaar_number":
        digits = re.sub(r"[ -]", "", raw_value)
        return digits if is_valid_aadhaar(digits) else None
    if key == "pan_number":
        pan = raw_value.strip().upper()
        return pan if is_valid_pan(pan) else None
    if key == "account_number":
        digits = re.sub(r"[ -]", "", raw_value)
        return digits if digits.isdigit() and 9 <= len(digits) <= 18 else None
    if key == "policy_number":
        policy = re.sub(r"\s", "", raw_value).upper()
        return policy if re.fullmatch(r"[A-Z0-9/-]{6,30}", policy) and re.search(r"\d", policy) else None
    if key == "ifsc":
        ifsc = raw_value.strip().upper()
        return ifsc if is_valid_ifsc(ifsc) else None
    if key == "disability_percent":
        cleaned = raw_value.strip().rstrip("%").strip()
        return int(cleaned) if cleaned.isdigit() and 0 <= int(cleaned) <= 100 else None
    return raw_value.strip()


def evaluate_rule(actual: Any, operator: str, expected: Any) -> bool:
    if operator == ">=":
        return actual >= expected
    if operator == "<=":
        return actual <= expected
    if operator == "==":
        return actual == expected
    raise ValueError(f"Unsupported rule operator: {operator}")
