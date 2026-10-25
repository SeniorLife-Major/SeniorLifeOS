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


# Which document types can prove what, and the confirmed field that does the proving
# (plan §4.4; Decision #32). A scheme can ask for "proof_of_age" instead of one fixed type.
# Only types and fields this phase reads are listed; e.g. an Aadhaar address is not read
# yet, so identity is not a proof of address.
PROOFS = {
    "proof_of_identity": (("identity", "name"),),
    "proof_of_age": (("identity", "date_of_birth"),),
    "proof_of_income": (("income_certificate", "annual_income"),),
    "proof_of_residence": (("residence_proof", "residence_state"),),
    "proof_of_disability": (("disability_certificate", "disability_percent"),),
    "health_cover": (("health_insurance", "policy_number"),),
    "bank_details": (("bank_passbook", "account_number"),),
}


def match_proofs(
    proofs: list[str],
    documents: list[dict[str, Any]],
    owner_id: str,
    as_of: str,
) -> list[dict[str, Any]]:
    """For each proof, the best of the owner's documents of any accepted type.

    Uses match_requirements for each accepted type, so the same rules apply (owner, confirmed,
    required fields, expiry). Each proving field is one of its type's required fields (a test
    checks this), so a usable document always has it confirmed with a source.
    Status: usable, unconfirmed, expired or missing; usable wins over the others.
    An unknown proof name raises KeyError instead of quietly reporting it missing.
    """
    results = []
    for proof in proofs:
        best = {"proof": proof, "document_type": None, "document_id": None, "status": "missing"}
        for document_type, _field in PROOFS[proof]:
            match = match_requirements([document_type], documents, owner_id, as_of)[0]
            status = match["status"]
            rank = MATCH_RANK.get(status, len(MATCH_RANK))
            if rank < MATCH_RANK.get(best["status"], len(MATCH_RANK)):
                best = {"proof": proof, "document_type": document_type, "document_id": match["document_id"],
                        "status": status}
        results.append(best)
    return results
