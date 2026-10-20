"""Shared document rules and requirement matching (plan §4.4).

One place for the fields each document type needs, plus the matcher that
answers "is a usable document of this type in the vault?". Part 2 (benefits)
and the future API should call match_requirements instead of re-implementing it.

The two graphs still keep their own copies of these rules; tests check that
every copy agrees with this module.
"""

from __future__ import annotations

from datetime import date
from typing import Any


REQUIRED_FIELDS = {
    "identity": {"name", "date_of_birth"},
    "income_certificate": {"name", "annual_income", "issue_date"},
    "residence_proof": {"name", "residence_state", "issue_date"},
    "health_insurance": {"name", "insurer", "policy_number", "expiry_date", "sum_insured"},
    "bank_passbook": {"name", "bank_name", "account_number", "ifsc"},
    "disability_certificate": {"name", "issuing_authority", "issue_date", "disability_percent"},
    "lab_report": {"name", "lab_name", "report_date", "tests"},
    "prescription": {"name", "doctor_name", "prescription_date", "medicines"},
    "medical_bill": {"name", "provider", "bill_date", "total_amount"},
}
DOCUMENT_TYPES = tuple(REQUIRED_FIELDS)
MATCH_RANK = {"usable": 0, "unconfirmed": 1, "expired": 2}


def match_requirements(
    required_types: list[str],
    documents: list[dict[str, Any]],
    owner_id: str,
    as_of: str,
) -> list[dict[str, Any]]:
    """For each required type, pick the owner's best document and say if it is usable.

    Status per type: usable, unconfirmed, expired, or missing. When several
    documents fit, usable beats unconfirmed, which beats expired.
    """
    matches = []
    today = date.fromisoformat(as_of)
    for required_type in required_types:
        candidates = [d for d in documents if d.get("type") == required_type and d.get("owner_id") == owner_id]
        status = "missing"
        selected_id = None
        if candidates:
            ranked = []
            for candidate in candidates:
                fields = candidate.get("confirmed_fields", {})
                complete = all(
                    fields.get(key, {}).get("value") is not None and fields.get(key, {}).get("source_ref")
                    for key in REQUIRED_FIELDS[required_type]
                )
                candidate_status = "usable" if candidate.get("status") == "confirmed" and complete else "unconfirmed"
                expiry = candidate.get("expiry_date")
                if expiry:
                    try:
                        if date.fromisoformat(expiry) < today:
                            candidate_status = "expired"
                    except ValueError:
                        candidate_status = "unconfirmed"
                ranked.append((candidate_status, candidate))
            status, chosen = min(ranked, key=lambda pair: MATCH_RANK[pair[0]])
            selected_id = chosen.get("document_id")
        matches.append({"required_type": required_type, "document_id": selected_id, "status": status})
    return matches
