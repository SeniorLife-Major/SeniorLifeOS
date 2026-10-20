"""Synthetic states for immediately running the two graphs with invoke()."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def document_success_state() -> dict[str, Any]:
    return {
        "actor_id": "senior-meera",
        "document_id": "doc-income-001",
        "claimed_type": "income_certificate",
        "document": {
            "owner_id": "senior-meera",
            "owner_name": "Meera Rao",
            "filename": "income_certificate_photo.jpg",
            "mime_type": "image/jpeg",
            "size_bytes": 145_000,
            "as_of": "2026-09-22",
            # Local OCR fixture: the graph reads these lines as if OCR returned them.
            "pages": [{
                "page": 1,
                "quality": 0.92,
                "text": "Name: Meera Rao\nAnnual income: ₹1,20,000\nIssue date: 2026/06/01\nIssuer: Demo District Office",
            }],
        },
        "review_decision": {
            "action": "confirm",
            "reviewer_id": "senior-meera",
            "edits": {"issue_date": "2026-06-01"},
        },
        "simulate_ocr_failures": 1,
    }


def document_failure_state() -> dict[str, Any]:
    state = document_success_state()
    state["document_id"] = "doc-income-blurry"
    state["document"]["pages"][0]["quality"] = 0.25
    state["review_decision"] = {"action": "confirm", "reviewer_id": "senior-meera"}
    state["simulate_ocr_failures"] = 0
    return state


def _upload_state(document_id: str, filename: str, text: str) -> dict[str, Any]:
    """A clean upload with no prefilled decision, so the confidence gate can auto-accept it."""
    return {
        "actor_id": "senior-meera",
        "document_id": document_id,
        "document": {
            "owner_id": "senior-meera",
            "owner_name": "Meera Rao",
            "filename": filename,
            "mime_type": "image/jpeg",
            "size_bytes": 120_000,
            "as_of": "2026-09-22",
            "pages": [{"page": 1, "quality": 0.95, "text": text}],
        },
    }


def insurance_state() -> dict[str, Any]:
    return _upload_state("doc-insurance-001", "health_insurance_card.jpg", (
        "Name: Meera Rao\nInsurer: Demo Health Insurance (fictional)\nPolicy No: HLT-2026/004512\n"
        "Sum insured: ₹5,00,000\nValid till: 2027-03-31"
    ))


def passbook_state() -> dict[str, Any]:
    return _upload_state("doc-passbook-001", "bank_passbook_page1.jpg", (
        "Name: Meera Rao\nBank name: Demo Bank (fictional)\nAccount number: 123456789012345\nIFSC: DEMO0001234"
    ))


def disability_state() -> dict[str, Any]:
    return _upload_state("doc-disability-001", "disability_certificate.jpg", (
        "Name: Meera Rao\nIssuing authority: Demo District Medical Board\nIssue date: 2025-11-10\n"
        "Disability percentage: 40%"
    ))


def lab_report_state() -> dict[str, Any]:
    """Fictional lab: heading as lab name, a results table, every row needs the owner's check."""
    return _upload_state("doc-lab-001", "lab_report.jpg", (
        "DEMO DIAGNOSTICS LAB (FICTIONAL)\n"
        "Patient Name: Meera Rao\nReported On: 2026-09-10\n"
        "Test Result Unit Reference Range\n"
        "Haemoglobin 11.2 g/dL 12.0 - 15.5\n"
        "Fasting Blood Sugar 98 mg/dL 70 - 100\n"
        "Total Cholesterol 232 mg/dL < 200\n"
        "*** End of Report ***"
    ))


def prescription_state() -> dict[str, Any]:
    return _upload_state("doc-rx-001", "prescription.jpg", (
        "Dr. Demo Physician, MBBS (fictional)\n"
        "Patient: Meera Rao\nDate: 15/09/2026\n"
        "Rx\n"
        "1. Tab. Metformin 500 mg 1-0-1 x 30 days\n"
        "2. Tab. Amlodipine 5 mg OD x 30 days"
    ))


def medical_bill_state() -> dict[str, Any]:
    return _upload_state("doc-bill-001", "pharmacy_bill.jpg", (
        "DEMO PHARMACY (FICTIONAL)\n"
        "Patient Name: Meera Rao\nBill Date: 16/09/2026\n"
        "Description Qty Amount\n"
        "Metformin 500 mg strip 2 120.00\n"
        "Amlodipine 5 mg strip 1 45.50\n"
        "Grand Total: ₹165.50"
    ))


def _existing_document(document_id: str, document_type: str, fields: dict[str, Any]) -> dict[str, Any]:
    return {
        "document_id": document_id,
        "owner_id": "senior-meera",
        "type": document_type,
        "status": "confirmed",
        "confirmed_fields": {
            key: {"value": value, "source_ref": f"document:{document_id}:page:1:line:{line}", "reviewed_by": "senior-meera"}
            for line, (key, value) in enumerate(fields.items(), start=1)
        },
        "expiry_date": None,
        "demo_only": True,
    }


def benefits_success_state(income_vault_record: dict[str, Any]) -> dict[str, Any]:
    """Pass the successful document graph's vault_record into this state."""
    return {
        "actor_id": "senior-meera",
        "case_id": "benefit-case-001",
        "case_version": 1,
        "expected_case_version": 1,
        "need_text": "Help me apply for a pension",
        "scheme_id": "demo_senior_support",
        "jurisdiction": "MH",
        "as_of": "2026-09-22",
        "profile_facts": {},
        "documents": [
            _existing_document("doc-identity-001", "identity", {
                "name": "Meera Rao", "date_of_birth": "1958-05-15",
            }),
            _existing_document("doc-residence-001", "residence_proof", {
                "name": "Meera Rao", "residence_state": "MH", "issue_date": "2026-02-01",
            }),
            deepcopy(income_vault_record),
        ],
        "review_decision": {"action": "approve", "reviewer_id": "senior-meera"},
    }


def benefits_failure_state() -> dict[str, Any]:
    """No income certificate: income is unknown and the document is missing."""
    state = benefits_success_state(_existing_document("placeholder", "income_certificate", {
        "name": "Meera Rao", "annual_income": 120000, "issue_date": "2026-06-01",
    }))
    state["case_id"] = "benefit-case-missing-income"
    state["documents"] = [d for d in state["documents"] if d["type"] != "income_certificate"]
    state.pop("review_decision")
    return state
