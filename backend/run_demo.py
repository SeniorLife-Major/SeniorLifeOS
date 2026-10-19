"""Run successful and edge-case document/benefits graphs with invoke()."""

from pprint import pprint

from seniorlife_os.benefits_graph import build_benefits_graph as benefits_graph
from seniorlife_os.document_graph import build_document_graph as document_graph
from seniorlife_os.demo_cases import (
    benefits_failure_state,
    benefits_success_state,
    document_failure_state,
    document_success_state,
)


def show(label: str, result: dict) -> None:
    print(f"\n=== {label} ===")
    print("status:", result["status"])
    print("node path:", " -> ".join(result.get("events", [])))
    for key in ("extracted_fields", "normalized_fields", "review_flags", "warnings", "errors",
                "missing_inputs", "contradictions", "criterion_results", "document_matches",
                "assessment", "vault_record", "application_package", "created_tasks"):
        if result.get(key):
            print(f"{key}:")
            pprint(result[key], sort_dicts=False)


if __name__ == "__main__":
    document_ok = document_graph().invoke(document_success_state())
    show("Document Intake - success with corrected OCR date", document_ok)

    document_bad = document_graph().invoke(document_failure_state())
    show("Document Intake - blurry upload", document_bad)

    benefits_ok = benefits_graph().invoke(benefits_success_state(document_ok["vault_record"]))
    show("Benefits Case Manager - successful manual handoff", benefits_ok)

    benefits_bad = benefits_graph().invoke(benefits_failure_state())
    show("Benefits Case Manager - missing income evidence", benefits_bad)
