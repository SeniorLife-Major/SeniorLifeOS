"""Document intake graph: validate -> classify -> extract -> review -> publish.

The page text is a local OCR fixture, so the graph needs no API key. The
review node supports both a prefilled decision for one-call demos and a real
LangGraph interrupt when built with a checkpointer.
"""

from __future__ import annotations

from datetime import date
from typing import Any, TypedDict
import os

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .demo_tools import extract_fields_from_pages, normalize_field


REQUIRED_FIELDS = {
    "identity": {"name", "date_of_birth"},
    "income_certificate": {"name", "annual_income", "issue_date"},
    "residence_proof": {"name", "residence_state", "issue_date"},
}
ALLOWED_MIME_TYPES = {"application/pdf", "image/png", "image/jpeg"}


class DocumentState(TypedDict, total=False):
    # Handwritten inputs
    actor_id: str
    document_id: str
    document: dict[str, Any]
    claimed_type: str
    review_decision: dict[str, Any]
    use_interrupt: bool
    use_llm: bool
    simulate_ocr_failures: int

    # Graph outputs / control
    document_type: str
    extracted_fields: dict[str, dict[str, Any]]
    normalized_fields: dict[str, Any]
    review_flags: list[str]
    vault_record: dict[str, Any]
    events: list[str]
    warnings: list[str]
    errors: list[str]
    retry_count: int
    status: str
    route: str


def add_event(state: DocumentState, name: str) -> list[str]:
    return [*state.get("events", []), name]


def validate_upload(state: DocumentState) -> dict[str, Any]:
    document = state.get("document") or {}
    errors = []
    if not state.get("actor_id") or not state.get("document_id"):
        errors.append("actor_id and document_id are required")
    if document.get("owner_id") != state.get("actor_id"):
        errors.append("Document owner does not match the acting senior")
    if document.get("mime_type") not in ALLOWED_MIME_TYPES:
        errors.append("Unsupported file type")
    if not isinstance(document.get("size_bytes"), int) or not 0 < document["size_bytes"] <= 5_000_000:
        errors.append("File must be between 1 byte and 5 MB")
    if not document.get("pages"):
        errors.append("No readable page fixture was supplied")
    return {
        "errors": errors,
        "status": "rejected" if errors else "processing",
        "route": "stop" if errors else "continue",
        "events": add_event(state, "validate_upload"),
    }


def classify_document(state: DocumentState) -> dict[str, Any]:
    claimed = state.get("claimed_type") or state["document"].get("claimed_type")
    filename = state["document"].get("filename", "").lower()
    guessed = claimed
    if not guessed and state.get("use_llm"):
        try:
            from .llm_agents import choose_document_type

            page_text = "\n".join(page.get("text", "") for page in state["document"]["pages"])
            guessed = choose_document_type(page_text).document_type
        except Exception as exc:
            return {
                "status": "failed_tool",
                "route": "stop",
                "errors": [f"Document classification LLM unavailable: {exc}"],
                "events": add_event(state, "classify_document:llm_error"),
            }
    if not guessed and not state.get("use_llm"):
        if "income" in filename:
            guessed = "income_certificate"
        elif "identity" in filename:
            guessed = "identity"
        elif "residence" in filename:
            guessed = "residence_proof"
    if guessed not in REQUIRED_FIELDS:
        return {
            "status": "needs_classification",
            "route": "stop",
            "warnings": ["Choose identity, income_certificate, or residence_proof"],
            "events": add_event(state, "classify_document:unknown"),
        }
    return {
        "document_type": guessed,
        "route": "continue",
        "events": add_event(state, f"classify_document:{guessed}"),
    }


def extract_content(state: DocumentState) -> dict[str, Any]:
    retries = state.get("retry_count", 0)
    if retries < state.get("simulate_ocr_failures", 0):
        retries += 1
        return {
            "retry_count": retries,
            "route": "retry" if retries < 3 else "stop",
            "status": "processing" if retries < 3 else "failed",
            "errors": [] if retries < 3 else ["Simulated OCR provider remained unavailable"],
            "events": add_event(state, f"extract_content:retry_{retries}"),
        }
    pages = state["document"]["pages"]
    if any(page.get("quality", 1.0) < 0.5 for page in pages):
        return {
            "route": "stop",
            "status": "needs_reupload",
            "warnings": ["At least one page is too blurry for a reliable extraction"],
            "events": add_event(state, "extract_content:unreadable"),
        }
    fields = extract_fields_from_pages(pages)
    if not fields:
        return {
            "route": "stop",
            "status": "needs_reupload",
            "warnings": ["No labelled fields could be extracted from the demo page text"],
            "events": add_event(state, "extract_content:no_fields"),
        }
    return {
        "extracted_fields": fields,
        "retry_count": retries,
        "route": "continue",
        "events": add_event(state, "extract_content:success"),
    }


def validate_candidates(state: DocumentState) -> dict[str, Any]:
    fields = state["extracted_fields"]
    normalized = {key: normalize_field(key, item["value"]) for key, item in fields.items()}
    flags = []
    for key in sorted(REQUIRED_FIELDS[state["document_type"]]):
        if normalized.get(key) is None or normalized.get(key) == "":
            flags.append(f"{key}: missing or invalid")
    owner_name = state["document"].get("owner_name")
    if owner_name and normalized.get("name") and owner_name.casefold() != str(normalized["name"]).casefold():
        flags.append("name differs from the provided owner name")
    today = date.fromisoformat(state["document"].get("as_of", date.today().isoformat()))
    if normalized.get("issue_date") and date.fromisoformat(normalized["issue_date"]) > today:
        flags.append("issue_date is in the future")
    if normalized.get("expiry_date") and date.fromisoformat(normalized["expiry_date"]) < today:
        flags.append("document is expired")
    return {
        "normalized_fields": normalized,
        "review_flags": flags,
        "events": add_event(state, "validate_candidates"),
    }


def human_field_review(state: DocumentState) -> dict[str, Any]:
    decision = state.get("review_decision")
    if not decision and state.get("use_interrupt"):
        # This node has no side effects before interrupt; it can safely restart.
        decision = interrupt({
            "document_id": state["document_id"],
            "document_type": state["document_type"],
            "candidates": state["normalized_fields"],
            "flags": state["review_flags"],
            "question": "Confirm, edit, or reject these extracted fields",
        })
    if not decision:
        return {
            "status": "needs_review",
            "route": "stop",
            "events": add_event(state, "human_field_review:pending"),
        }
    if decision.get("reviewer_id") != state.get("actor_id"):
        return {
            "status": "rejected",
            "route": "stop",
            "errors": ["Only the document owner can confirm this demo document"],
            "events": add_event(state, "human_field_review:unauthorized"),
        }
    if decision.get("action") == "reject":
        return {
            "status": "rejected",
            "route": "stop",
            "events": add_event(state, "human_field_review:rejected"),
        }
    if decision.get("action") != "confirm":
        return {
            "status": "needs_review",
            "route": "stop",
            "errors": ["Decision action must be confirm or reject"],
            "events": add_event(state, "human_field_review:invalid_decision"),
        }
    edits = decision.get("edits", {})
    if any(key not in state["extracted_fields"] for key in edits):
        return {
            "status": "needs_review",
            "route": "stop",
            "errors": ["Edits may only correct extracted fields in this prototype"],
            "events": add_event(state, "human_field_review:invalid_edit"),
        }
    confirmed = dict(state["normalized_fields"])
    for key, raw_value in edits.items():
        confirmed[key] = normalize_field(key, str(raw_value))
    missing = [key for key in REQUIRED_FIELDS[state["document_type"]] if confirmed.get(key) is None or confirmed.get(key) == ""]
    if missing:
        return {
            "status": "needs_review",
            "route": "stop",
            "errors": [f"Correct these required fields: {', '.join(sorted(missing))}"],
            "events": add_event(state, "human_field_review:missing_fields"),
        }
    today = date.fromisoformat(state["document"].get("as_of", date.today().isoformat()))
    if confirmed.get("issue_date") and date.fromisoformat(confirmed["issue_date"]) > today:
        return {
            "status": "needs_review",
            "route": "stop",
            "errors": ["Issue date cannot be in the future"],
            "events": add_event(state, "human_field_review:future_issue_date"),
        }
    return {
        "normalized_fields": confirmed,
        "review_decision": decision,
        "status": "reviewed",
        "route": "continue",
        "events": add_event(state, "human_field_review:confirmed"),
    }


def publish_confirmed_version(state: DocumentState) -> dict[str, Any]:
    fields = state["normalized_fields"]
    extracted = state["extracted_fields"]
    decision = state["review_decision"]
    confirmed_fields = {
        key: {
            "value": value,
            "source_ref": f"document:{state['document_id']}:{extracted[key]['source_ref']}",
            "reviewed_by": decision["reviewer_id"],
            "corrected_by_user": key in decision.get("edits", {}),
        }
        for key, value in fields.items()
    }
    record = {
        "document_id": state["document_id"],
        "owner_id": state["actor_id"],
        "type": state["document_type"],
        "status": "confirmed",
        "confirmed_fields": confirmed_fields,
        "issue_date": fields.get("issue_date"),
        "expiry_date": fields.get("expiry_date"),
        "demo_only": True,
    }
    return {
        "vault_record": record,
        "status": "confirmed",
        "route": "done",
        "events": add_event(state, "publish_confirmed_version"),
    }


def route_step(state: DocumentState) -> str:
    return state.get("route", "stop")


def build_document_graph(checkpointer: Any = None):
    graph = StateGraph(DocumentState)
    graph.add_node("validate_upload", validate_upload)
    graph.add_node("classify_document", classify_document)
    graph.add_node("extract_content", extract_content)
    graph.add_node("validate_candidates", validate_candidates)
    graph.add_node("human_field_review", human_field_review)
    graph.add_node("publish_confirmed_version", publish_confirmed_version)

    graph.add_edge(START, "validate_upload")
    graph.add_conditional_edges("validate_upload", route_step, {"continue": "classify_document", "stop": END})
    graph.add_conditional_edges("classify_document", route_step, {"continue": "extract_content", "stop": END})
    graph.add_conditional_edges("extract_content", route_step, {
        "continue": "validate_candidates", "retry": "extract_content", "stop": END,
    })
    graph.add_edge("validate_candidates", "human_field_review")
    graph.add_conditional_edges("human_field_review", route_step, {
        "continue": "publish_confirmed_version", "stop": END,
    })
    graph.add_edge("publish_confirmed_version", END)

    return graph.compile(checkpointer=checkpointer)


# Plain invoke(initial_state) works when review_decision is already supplied.
if __name__ == '__main__':
    document_graph = build_document_graph()
    if not os.path.exists('doc_graph.png'):
        from IPython.display import display, Image
        img = Image(document_graph.get_graph().draw_mermaid_png())
        with open('doc_graph.png', 'wb') as file:
            file.write(img.data)
