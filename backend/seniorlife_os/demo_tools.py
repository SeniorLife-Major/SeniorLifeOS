"""Small local tool adapters. Replace these with OCR/catalogue services later.

The benefit scheme is fictional. Its criteria are useful for exercising graph
branches, not advice about an actual Indian government programme.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any


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
}


def extract_fields_from_pages(pages: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Simulated OCR + field extraction from handwritten demo page text.

    Each field carries a page and line reference. No real PDF/image OCR occurs.
    """
    found: dict[str, dict[str, Any]] = {}
    for page in pages:
        for line_number, line in enumerate(page.get("text", "").splitlines(), start=1):
            if ":" not in line:
                continue
            raw_label, raw_value = line.split(":", 1)
            key = FIELD_LABELS.get(raw_label.strip().lower())
            value = raw_value.strip()
            if key and value:
                found[key] = {
                    "value": value,
                    "page": page["page"],
                    "line": line_number,
                    "source_ref": f"page:{page['page']}:line:{line_number}",
                    "confidence": page.get("quality", 1.0),
                }
    return found


def normalize_field(key: str, raw_value: str) -> Any:
    """Normalize known fields without guessing unclear values."""
    if key == "annual_income":
        cleaned = re.sub(r"[₹,\s]", "", raw_value)
        return int(cleaned) if cleaned.isdigit() else None
    if key in {"issue_date", "expiry_date", "date_of_birth"}:
        try:
            return date.fromisoformat(raw_value).isoformat()
        except ValueError:
            return None
    if key == "residence_state":
        return raw_value.upper()
    return raw_value.strip()


def evaluate_rule(actual: Any, operator: str, expected: Any) -> bool:
    if operator == ">=":
        return actual >= expected
    if operator == "<=":
        return actual <= expected
    if operator == "==":
        return actual == expected
    raise ValueError(f"Unsupported rule operator: {operator}")
