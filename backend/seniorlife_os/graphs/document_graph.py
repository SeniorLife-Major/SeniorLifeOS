"""Document intake graph: validate -> read file -> classify -> extract -> gate -> review -> protect -> publish.

Pages come from a real file (document.file_path, read by local OCR in tools/ocr.py)
or from handwritten page fixtures (document.pages). Neither needs an API key. The
review node supports both a prefilled decision for one-call demos and a real
LangGraph interrupt when built with a checkpointer.
"""

from __future__ import annotations

from datetime import date
from typing import Any, TypedDict
import os

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from ..tools.demo_tools import extract_fields_from_pages, normalize_field
from ..tools.ocr import OcrEngineError, OcrError, read_document
from ..tools.ocr_postprocess import clean_page, lookalike_key, repair_value
from ..utils.privacy import ID_FIELDS, keyed_hash, load_hash_key, mask_identifier
from ..utils.tracing import install_safe_client


REQUIRED_FIELDS = {
    "identity": {"name", "date_of_birth"},
    "income_certificate": {"name", "annual_income", "issue_date"},
    "residence_proof": {"name", "residence_state", "issue_date"},
    "health_insurance": {"name", "insurer", "policy_number", "expiry_date", "sum_insured"},
    "bank_passbook": {"name", "bank_name", "account_number", "ifsc"},
    "disability_certificate": {"name", "issuing_authority", "issue_date", "disability_percent"},
}
ALLOWED_MIME_TYPES = {"application/pdf", "image/png", "image/jpeg"}
CONFIDENCE_THRESHOLD = 0.9
AUTO_REVIEWER_ID = "system:confidence_gate"
MAX_UPLOAD_BYTES = 5_000_000
OCR_MAX_ATTEMPTS = 3


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
    vault_identifier_hashes: dict[str, str]  # keyed hash -> document_id already in the vault

    # Graph outputs / control
    document_type: str
    extracted_fields: dict[str, dict[str, Any]]
    normalized_fields: dict[str, Any]
    review_flags: list[str]
    identifier_hashes: dict[str, str]
    vault_record: dict[str, Any]
    events: list[str]
    warnings: list[str]
    errors: list[str]
    retry_count: int
    ocr_attempts: int
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
    file_path = document.get("file_path")
    size = document.get("size_bytes")
    if file_path:
        # A real upload: trust the file on disk, not the size the client claimed.
        if document.get("pages"):
            errors.append("Give either file_path or page fixtures, not both")
        size = os.path.getsize(file_path) if os.path.isfile(file_path) else None
        if size is None:
            errors.append("Uploaded file not found")
    elif not document.get("pages"):
        errors.append("No readable page fixture was supplied")
    missing_file = file_path and size is None  # already reported above
    if not missing_file and (not isinstance(size, int) or not 0 < size <= MAX_UPLOAD_BYTES):
        errors.append("File must be between 1 byte and 5 MB")
    return {
        "errors": errors,
        "status": "rejected" if errors else "processing",
        "route": "stop" if errors else "continue",
        "events": add_event(state, "validate_upload"),
    }


def read_file(state: DocumentState) -> dict[str, Any]:
    """Turn an uploaded file into page text with local OCR. Page fixtures skip this."""
    document = state["document"]
    if not document.get("file_path"):
        return {"route": "continue", "events": add_event(state, "read_file:fixture")}
    attempts = state.get("ocr_attempts", 0) + 1
    try:
        pages = read_document(document["file_path"], document["mime_type"])
    except OcrEngineError:
        if attempts < OCR_MAX_ATTEMPTS:
            return {
                "ocr_attempts": attempts,
                "route": "retry",
                "events": add_event(state, f"read_file:retry_{attempts}"),
            }
        return {
            "ocr_attempts": attempts,
            "status": "failed_tool",
            "route": "stop",
            "errors": [f"The OCR engine failed {attempts} times"],
            "events": add_event(state, "read_file:engine_failed"),
        }
    except OcrError as exc:
        # The file itself is the problem (broken, wrong type, too many pages): retrying won't help.
        return {
            "ocr_attempts": attempts,
            "status": "needs_reupload",
            "route": "stop",
            "warnings": [str(exc)],
            "events": add_event(state, "read_file:unreadable"),
        }
    pages = [clean_page(page) for page in pages]
    if not any(page["text"].strip() for page in pages):
        return {
            "ocr_attempts": attempts,
            "status": "needs_reupload",
            "route": "stop",
            "warnings": ["No text could be read from the file"],
            "events": add_event(state, "read_file:no_text"),
        }
    return {
        "document": {**document, "pages": pages},
        "ocr_attempts": attempts,
        "route": "continue",
        "events": add_event(state, "read_file:" + "+".join(dict.fromkeys(page["engine"] for page in pages))),
    }


def classify_document(state: DocumentState) -> dict[str, Any]:
    claimed = state.get("claimed_type") or state["document"].get("claimed_type")
    filename = state["document"].get("filename", "").lower()
    guessed = claimed
    if not guessed and state.get("use_llm"):
        try:
            from ..agents.llm_agents import choose_document_type

            page_text = "\n".join(page.get("text", "") for page in state["document"]["pages"])
            known = {"NAME": state["document"].get("owner_name", "")}
            guessed = choose_document_type(page_text, known).document_type
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
        elif "insurance" in filename:
            guessed = "health_insurance"
        elif "passbook" in filename:
            guessed = "bank_passbook"
        elif "disability" in filename:
            guessed = "disability_certificate"
    if guessed not in REQUIRED_FIELDS:
        return {
            "status": "needs_classification",
            "route": "stop",
            "warnings": [f"Choose one of: {', '.join(REQUIRED_FIELDS)}"],
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
    fields = extract_fields_from_pages(pages, state["document_type"])
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
    fields = {key: dict(item) for key, item in state["extracted_fields"].items()}
    normalized = {}
    flags = []
    for key, item in fields.items():
        if item.get("layout"):
            # Found by a layout guess (no colon, or label above the value): the owner checks it.
            flags.append(f"{key}: found by layout ({item['layout'].replace('_', ' ')}), please check")
        normalized[key] = normalize_field(key, item["value"])
        if normalized[key] is not None:
            continue
        # The value fails its check as read: try look-alike swaps where the field's shape
        # demands a digit or a letter, and cutting extra text after a strictly shaped value.
        # Keep a repair only if it then passes, and always send it to review.
        repaired = repair_value(key, item["value"])
        if repaired:
            item["ocr_value"] = item["value"]  # kept so the masking step can hide this spelling too
            item["value"], normalized[key], item["corrections"] = repaired
            flags.extend(f"{key}: {note}, please check" for note in repaired[2])
    for key in sorted(REQUIRED_FIELDS[state["document_type"]]):
        if normalized.get(key) is None or normalized.get(key) == "":
            flags.append(f"{key}: missing or invalid")
    for key in ID_FIELDS:
        if key in fields and normalized.get(key) is None:
            flags.append(f"{key}: failed format or checksum check")
    owner_name = state["document"].get("owner_name")
    if owner_name and normalized.get("name") and owner_name.casefold() != str(normalized["name"]).casefold():
        # Names are never auto-corrected ("lyer" may really be a name); only explain the likely cause.
        if lookalike_key(owner_name) == lookalike_key(str(normalized["name"])):
            flags.append("name differs from the provided owner name only by look-alike letters (I/l/1, O/0)")
        else:
            flags.append("name differs from the provided owner name")
    today = date.fromisoformat(state["document"].get("as_of", date.today().isoformat()))
    if normalized.get("issue_date") and date.fromisoformat(normalized["issue_date"]) > today:
        flags.append("issue_date is in the future")
    if normalized.get("expiry_date") and date.fromisoformat(normalized["expiry_date"]) < today:
        flags.append("document is expired")
    return {
        "extracted_fields": fields,
        "normalized_fields": normalized,
        "review_flags": flags,
        "events": add_event(state, "validate_candidates"),
    }


def confidence_gate(state: DocumentState) -> dict[str, Any]:
    # An explicit owner decision always wins over automatic acceptance.
    if state.get("review_decision"):
        return {"route": "review", "events": add_event(state, "confidence_gate:owner_decision")}
    flags = list(state["review_flags"])
    for key, item in sorted(state["extracted_fields"].items()):
        if item.get("confidence", 0.0) < CONFIDENCE_THRESHOLD:
            flags.append(f"{key}: low confidence ({item.get('confidence', 0.0):.2f})")
    if flags:
        return {
            "review_flags": flags,
            "route": "review",
            "events": add_event(state, "confidence_gate:needs_review"),
        }
    return {
        "review_decision": {"action": "auto_accept", "reviewer_id": AUTO_REVIEWER_ID},
        "status": "reviewed",
        "route": "auto_accept",
        "events": add_event(state, "confidence_gate:auto_accept"),
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


def protect_identifiers(state: DocumentState) -> dict[str, Any]:
    # Runs after review, so the reviewer could compare the full number with the card.
    fields = state["normalized_fields"]
    present = [key for key in ID_FIELDS if fields.get(key)]
    if not present:
        return {"route": "continue", "events": add_event(state, "protect_identifiers:none")}
    try:
        key = load_hash_key()
    except RuntimeError as exc:
        # Fail closed: never publish a full ID number.
        return {
            "status": "failed_tool",
            "route": "stop",
            "errors": [str(exc)],
            "events": add_event(state, "protect_identifiers:no_key"),
        }
    protected = dict(fields)
    extracted = {name: dict(item) for name, item in state["extracted_fields"].items()}
    decision = dict(state["review_decision"])
    edits = dict(decision.get("edits", {}))
    hashes = {}
    replacements = {}  # every spelling of a full number -> its mask
    for field in present:
        hashes[field] = keyed_hash(field, fields[field], key)
        protected[field] = mask_identifier(field, fields[field])
        replacements[fields[field]] = protected[field]
        if field in extracted:
            replacements[str(extracted[field]["value"])] = protected[field]
            extracted[field]["value"] = protected[field]
            if "ocr_value" in extracted[field]:  # the spelling OCR read before a look-alike fix
                replacements[str(extracted[field]["ocr_value"])] = protected[field]
                extracted[field]["ocr_value"] = protected[field]
        if field in edits:
            replacements[str(edits[field])] = protected[field]
            edits[field] = protected[field]
    if "edits" in decision:
        decision["edits"] = edits
    # The OCR page text is later stored as searchable chunks, so mask it too,
    # including each OCR line (pages read from a real file carry a "lines" list).
    def mask_text(text: str) -> str:
        for original in sorted(replacements, key=len, reverse=True):
            text = text.replace(original, replacements[original])
        return text

    document = dict(state["document"])
    pages = []
    for page in document.get("pages", []):
        masked = {**page, "text": mask_text(page.get("text", ""))}
        if "lines" in page:
            # "parts" (pieces of a line, used only to find fields) are dropped: a number split
            # across two pieces could not be masked reliably piece by piece.
            masked["lines"] = [{**{k: v for k, v in line.items() if k != "parts"}, "text": mask_text(line["text"])}
                               for line in page["lines"]]
        pages.append(masked)
    document["pages"] = pages
    known = state.get("vault_identifier_hashes", {})
    warnings = [
        f"{field} already in the vault (document {known[digest]})"
        for field, digest in hashes.items() if digest in known
    ]
    return {
        "document": document,
        "normalized_fields": protected,
        "extracted_fields": extracted,
        "review_decision": decision,
        "identifier_hashes": hashes,
        "warnings": [*state.get("warnings", []), *warnings],
        "route": "continue",
        "events": add_event(state, "protect_identifiers:protected"),
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
        "identifier_hashes": state.get("identifier_hashes", {}),
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
    install_safe_client()  # if LangSmith tracing is on, only masked data is sent
    graph = StateGraph(DocumentState)
    graph.add_node("validate_upload", validate_upload)
    graph.add_node("read_file", read_file)
    graph.add_node("classify_document", classify_document)
    graph.add_node("extract_content", extract_content)
    graph.add_node("validate_candidates", validate_candidates)
    graph.add_node("confidence_gate", confidence_gate)
    graph.add_node("human_field_review", human_field_review)
    graph.add_node("protect_identifiers", protect_identifiers)
    graph.add_node("publish_confirmed_version", publish_confirmed_version)

    graph.add_edge(START, "validate_upload")
    graph.add_conditional_edges("validate_upload", route_step, {"continue": "read_file", "stop": END})
    graph.add_conditional_edges("read_file", route_step, {
        "continue": "classify_document", "retry": "read_file", "stop": END,
    })
    graph.add_conditional_edges("classify_document", route_step, {"continue": "extract_content", "stop": END})
    graph.add_conditional_edges("extract_content", route_step, {
        "continue": "validate_candidates", "retry": "extract_content", "stop": END,
    })
    graph.add_edge("validate_candidates", "confidence_gate")
    graph.add_conditional_edges("confidence_gate", route_step, {
        "auto_accept": "protect_identifiers", "review": "human_field_review",
    })
    graph.add_conditional_edges("human_field_review", route_step, {
        "continue": "protect_identifiers", "stop": END,
    })
    graph.add_conditional_edges("protect_identifiers", route_step, {
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
