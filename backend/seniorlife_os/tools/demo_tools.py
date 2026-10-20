"""Small local tool adapters. Replace these with OCR/catalogue services later.

The benefit scheme is fictional. Its criteria are useful for exercising graph
branches, not advice about an actual Indian government programme.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from ..utils.privacy import is_valid_aadhaar, is_valid_ifsc, is_valid_pan


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
    "date of birth": "date_of_birth",
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
    "a/c no": "account_number",
    "insurer": "insurer",
    "policy no": "policy_number",
    "policy number": "policy_number",
    "valid till": "expiry_date",
    "sum insured": "sum_insured",
    "bank": "bank_name",
    "bank name": "bank_name",
    "ifsc": "ifsc",
    "ifsc code": "ifsc",
    "issuing authority": "issuing_authority",
    "disability": "disability_percent",
    "disability percentage": "disability_percent",
}


def extract_fields_from_pages(pages: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Field extraction from "Label: value" lines of page text.

    The pages are either handwritten demo fixtures or the output of real OCR
    (tools/ocr.py). Each field carries a page and line reference.
    """
    found: dict[str, dict[str, Any]] = {}
    for page in pages:
        text_lines = page.get("text", "").splitlines()
        # Pages from real OCR (tools/ocr.py) carry one entry per text line with its own
        # confidence and box. Only trust them if they line up with the text.
        ocr_lines = page.get("lines")
        if not ocr_lines or len(ocr_lines) != len(text_lines):
            ocr_lines = None
        for line_number, line in enumerate(text_lines, start=1):
            if ":" not in line:
                continue
            raw_label, raw_value = line.split(":", 1)
            key = FIELD_LABELS.get(raw_label.strip().lower())
            value = raw_value.strip()
            if key and value:
                confidence = page.get("quality", 1.0)
                item = {
                    "value": value,
                    "page": page["page"],
                    "line": line_number,
                    "source_ref": f"page:{page['page']}:line:{line_number}",
                }
                if ocr_lines:
                    ocr_line = ocr_lines[line_number - 1]
                    # The line's own confidence; a blurry page keeps its lower cap.
                    confidence = ocr_line["confidence"]
                    if page.get("blurry"):
                        confidence = min(confidence, page.get("quality", 1.0))
                    item["box"] = ocr_line.get("box")
                item["confidence"] = confidence
                found[key] = item
    return found


def normalize_field(key: str, raw_value: str) -> Any:
    """Normalize known fields without guessing unclear values."""
    if key in {"annual_income", "sum_insured"}:
        cleaned = re.sub(r"[₹,\s]", "", raw_value)
        return int(cleaned) if cleaned.isdigit() else None
    if key in {"issue_date", "expiry_date", "date_of_birth"}:
        try:
            return date.fromisoformat(raw_value).isoformat()
        except ValueError:
            return None
    if key == "residence_state":
        return raw_value.upper()
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
