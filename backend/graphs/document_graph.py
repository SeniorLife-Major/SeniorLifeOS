"""Document intake graph: validate -> check file -> read file -> classify -> extract -> gate -> review -> protect -> publish.

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
from ..tools.medical import (
    LIST_FIELDS, MEDICAL_TYPES, extract_lists, heading_fields, looks_medical, medical_type_from_text,
)
from ..tools.ocr import OcrEngineError, OcrError, read_document
from ..tools.cloud_ocr import ENGINES, CloudOcrError, cloud_engine, engine_key, engine_label, read_document_in_cloud
from ..tools.file_safety import check_file as check_uploaded_file
from ..tools.free_text import free_text_problems
from ..tools.ocr_postprocess import clean_page, lookalike_key, repair_value
from ..utils.privacy import ID_FIELDS, keyed_hash, load_hash_key, mask_identifier, mask_ids_in_text
from ..utils.checkpoints import is_encrypted
from ..utils.tracing import install_safe_client


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
    medical_cloud_consent: bool  # the senior allows the cloud model to read medical documents
    cloud_ocr_consent: bool  # the senior allows the cloud OCR engine to read unclear pages again
    cloud_ocr_used: bool

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


def check_file(state: DocumentState) -> dict[str, Any]:
    """Look inside an uploaded file before anything reads it: real type, risky PDF content,
    size bombs, and a virus scan (tools/file_safety.py). Page fixtures skip this."""
    document = state["document"]
    if not document.get("file_path"):
        return {"route": "continue", "events": add_event(state, "check_file:fixture")}
    result = check_uploaded_file(document["file_path"], document["mime_type"])
    checked = {**document, "sha256": result.sha256, "virus_scan": result.scan}
    warnings = [*state.get("warnings", []), *result.warnings]
    if result.problems:
        return {"document": checked, "status": "rejected", "route": "stop", "errors": result.problems,
                "warnings": warnings, "events": add_event(state, "check_file:refused")}
    if result.reupload:
        # A broken or overlong file is a mistake, not a danger: ask for it again.
        return {"document": checked, "status": "needs_reupload", "route": "stop",
                "warnings": [*warnings, *result.reupload], "events": add_event(state, "check_file:reupload")}
    if result.scan_unavailable_refuses:
        return {"document": checked, "status": "failed_tool", "route": "stop",
                "errors": ["A virus scan is required but could not be made"], "warnings": warnings,
                "events": add_event(state, "check_file:scan_unavailable")}
    return {"document": checked, "warnings": warnings, "route": "continue",
            "events": add_event(state, f"check_file:{result.scan}")}


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


FILENAME_HINTS = (
    ("income", "income_certificate"), ("identity", "identity"), ("residence", "residence_proof"),
    ("insurance", "health_insurance"), ("passbook", "bank_passbook"), ("disability", "disability_certificate"),
    ("lab", "lab_report"), ("prescription", "prescription"), ("bill", "medical_bill"), ("invoice", "medical_bill"),
)


def guess_type_locally(filename: str, page_text: str) -> str | None:
    """Type from the file name, then (for medical documents) from words on the page. No network."""
    for hint, document_type in FILENAME_HINTS:
        if hint in filename:
            return document_type
    return medical_type_from_text(page_text)


def classify_document(state: DocumentState) -> dict[str, Any]:
    claimed = state.get("claimed_type") or state["document"].get("claimed_type")
    filename = state["document"].get("filename", "").lower()
    guessed = claimed
    page_text = "\n".join(page.get("text", "") for page in state["document"]["pages"])
    # A page that looks medical goes to the cloud model only with consent (Decision #29),
    # even just to learn its type; without consent it is classified on this machine.
    cloud_allowed = not looks_medical(page_text) or state.get("medical_cloud_consent")
    if not guessed and state.get("use_llm") and not cloud_allowed:
        guessed = guess_type_locally(filename, page_text)
    elif not guessed and state.get("use_llm"):
        try:
            from ..agents.llm_agents import choose_document_type

            known ={"NAME": state["document"].get("owner_name", "")}
            guessed = choose_document_type(page_text, known).document_type
        except Exception as exc:
            return {
                "status": "failed_tool",
                "route": "stop",
                "errors": [f"Document classification LLM unavailable: {exc}"],
                "events": add_event(state, "classify_document:llm_error"),
            }
    if not guessed and not state.get("use_llm"):
        guessed = guess_type_locally(filename, page_text)
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
    fields.update(extract_lists(pages, state["document_type"]))  # lab tests, medicines, bill items
    fields.update(heading_fields(pages, state["document_type"], fields))
    missing = [key for key in sorted(REQUIRED_FIELDS[state["document_type"]]) if key not in fields]
    # Medical documents go to the cloud model only with the senior's consent (Decision #29).
    cloud_allowed = state["document_type"] not in MEDICAL_TYPES or state.get("medical_cloud_consent")
    if missing and state.get("use_llm") and cloud_allowed:
        # Values inside sentences or with no label: let the model look for just these fields.
        return {
            "extracted_fields": fields,
            "retry_count": retries,
            "route": "llm",
            "events": add_event(state, "extract_content:missing_fields"),
        }
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


def find_line(pages: list[dict[str, Any]], value: str) -> tuple[dict[str, Any], int]:
    """The page and line number where a value appears (first word as a fallback)."""
    wanted = [" ".join(value.split()).casefold(), value.split()[0].casefold()]
    for target in wanted:
        for page in pages:
            for number, line in enumerate(page.get("text", "").splitlines(), start=1):
                if target in " ".join(line.split()).casefold():
                    return page, number
    return pages[0], 1


def llm_extract(state: DocumentState) -> dict[str, Any]:
    """Ask the model for required fields the rules missed. Everything it finds is reviewed."""
    fields = dict(state.get("extracted_fields", {}))
    document_type = state["document_type"]
    missing = [key for key in sorted(REQUIRED_FIELDS[document_type]) if key not in fields]
    pages = state["document"]["pages"]
    owner_name = state["document"].get("owner_name")
    warnings = list(state.get("warnings", []))
    try:
        from ..agents.llm_agents import extract_missing_fields

        found = extract_missing_fields("\n".join(page.get("text", "") for page in pages), document_type, missing,
                                       {"NAME": owner_name} if owner_name else None)
    except Exception as exc:  # no model, no key, network, or redaction failure: never guess
        found = {}
        warnings.append(f"AI extraction unavailable ({type(exc).__name__}); missing fields need the owner")
    def place(text: str) -> dict[str, Any]:
        """Where a value sits: page, line, that line's box and confidence."""
        page, number = find_line(pages, text)
        lines = page.get("lines")
        line = lines[number - 1] if lines and len(lines) == len(page.get("text", "").splitlines()) else {}
        confidence = line.get("confidence", page.get("quality", 1.0))
        if page.get("blurry"):
            confidence = min(confidence, page.get("quality", 1.0))
        return {"page": page["page"], "line": number, "source_ref": f"page:{page['page']}:line:{number}",
                "box": line.get("box"), "confidence": confidence}

    for key, value in found.items():
        if isinstance(value, list):  # rows of a medical list: each row placed on its own line
            rows = []
            for row in value:
                where = place(next(text for text in row.values() if text))
                rows.append({**row, "source_ref": where["source_ref"], "confidence": where["confidence"],
                             "box": where["box"]})
            first = place(next(text for text in value[0].values() if text))
            fields[key] = {**first, "value": rows, "confidence": min(row["confidence"] for row in rows),
                           "layout": "llm"}
        else:
            fields[key] = {**place(value), "value": value, "layout": "llm"}
    if not fields:
        return {
            "route": "stop",
            "status": "needs_reupload",
            "warnings": [*warnings, "No fields could be found in the document"],
            "events": add_event(state, "llm_extract:no_fields"),
        }
    return {
        "extracted_fields": fields,
        "warnings": warnings,
        "route": "continue",
        "events": add_event(state, f"llm_extract:found_{len(found)}_of_{len(missing)}"),
    }


LAYOUT_NOTES = {
    "no_colon": "found by layout (no colon)",
    "label_above": "found by layout (label above)",
    "llm": "found by AI reading the text",
    "heading": "taken from the page heading",
    "doctor_line": "taken from a line starting with Dr.",
}
FUTURE_DATE_LABELS = {"issue_date": "Issue date", "report_date": "Report date",
                      "prescription_date": "Prescription date", "bill_date": "Bill date"}


def is_blank(value: Any) -> bool:
    return value is None or value == "" or value == []


def validate_candidates(state: DocumentState) -> dict[str, Any]:
    fields = {key: dict(item) for key, item in state["extracted_fields"].items()}
    normalized = {}
    flags = []
    for key, item in fields.items():
        if item.get("layout"):
            # Found by a layout guess or by the model reading the text: the owner checks it.
            flags.append(f"{key}: {LAYOUT_NOTES[item['layout']]}, please check")
        if item.get("source") == "cloud_ocr":
            engine = engine_label(item.get("cloud_engine", "google_vision"))
            flags.append(f"{key}: read again by cloud OCR ({engine}), please check")
        normalized[key] = normalize_field(key, item["value"])
        if normalized[key] is None and isinstance(item["value"], str):
            # The value fails its check as read: try look-alike swaps where the field's shape
            # demands a digit or a letter, and cutting extra text after a strictly shaped value.
            # Keep a repair only if it then passes, and always send it to review.
            repaired = repair_value(key, item["value"])
            if repaired:
                item["ocr_value"] = item["value"]  # kept so the masking step can hide this spelling too
                item["value"], normalized[key], item["corrections"] = repaired
        # Flagged on every pass (validation runs again after a cloud read).
        flags.extend(f"{key}: {note}, please check" for note in item.get("corrections", []))
        # Organization names have no format; flag signs of a cut-off or misread word.
        flags.extend(f"{key}: {note}, please check" for note in free_text_problems(key, item["value"]))
    for key in sorted(REQUIRED_FIELDS[state["document_type"]]):
        if is_blank(normalized.get(key)):
            flags.append(f"{key}: missing or invalid")
    for key in sorted(LIST_FIELDS & set(normalized)):
        # Every row of a medical list is checked by the owner (Decision #29).
        rows = normalized[key] or []
        if rows:
            flags.append(f"{key}: please check every row ({len(rows)})")
        for number, row in enumerate(rows, start=1):
            flags.extend(f"{key} row {number}: {problem}" for problem in row.get("problems", []))
    items, total = normalized.get("items"), normalized.get("total_amount")
    if items and total is not None and all(row.get("amount") is not None for row in items):
        added = round(sum(row["amount"] for row in items), 2)
        if abs(added - total) > 0.5:
            flags.append(f"items add up to {added:.2f} but the total says {total:.2f}")
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
    for key in FUTURE_DATE_LABELS:
        if normalized.get(key) and date.fromisoformat(normalized[key]) > today:
            flags.append(f"{key} is in the future")
    if normalized.get("expiry_date") and date.fromisoformat(normalized["expiry_date"]) < today:
        flags.append("document is expired")
    return {
        "extracted_fields": fields,
        "normalized_fields": normalized,
        "review_flags": flags,
        "route": "cloud_ocr" if wants_cloud_ocr(state, fields, normalized) else "continue",
        "events": add_event(state, "validate_candidates"),
    }


def wants_cloud_ocr(state: DocumentState, fields: dict[str, Any], normalized: dict[str, Any]) -> bool:
    """Read the file again in the cloud? Only with consent, once, and only if the local reading
    left a required field missing or invalid, a field unsure or looking cut off or misread,
    or a page blurry."""
    document = state["document"]
    if not state.get("cloud_ocr_consent") or state.get("cloud_ocr_used") or not document.get("file_path"):
        return False
    if state["document_type"] in MEDICAL_TYPES and not state.get("medical_cloud_consent"):
        return False
    weak = any(is_blank(normalized.get(key)) for key in REQUIRED_FIELDS[state["document_type"]])
    weak = weak or any(item.get("confidence", 1.0) < CONFIDENCE_THRESHOLD for item in fields.values())
    weak = weak or any(free_text_problems(key, item.get("value")) for key, item in fields.items())
    return weak or any(page.get("blurry") for page in document.get("pages", []))


def cloud_ocr(state: DocumentState) -> dict[str, Any]:
    """Read the file again in the cloud (Google Vision, or a Groq vision model if
    SENIORLIFE_CLOUD_OCR=groq), with ID regions blacked out, and use its values for fields
    the local reading got wrong or was unsure of. ID fields always stay local."""
    done = {"cloud_ocr_used": True, "route": "continue"}
    warnings = list(state.get("warnings", []))
    try:
        engine = cloud_engine()
    except CloudOcrError as exc:
        return {**done, "warnings": [*warnings, f"Cloud OCR skipped: {exc}"],
                "events": add_event(state, "cloud_ocr:bad_setting")}
    key = engine_key(engine)
    if not key:
        return {**done, "warnings": [*warnings, f"Cloud OCR skipped: {ENGINES[engine][2]} is not set"],
                "events": add_event(state, "cloud_ocr:no_key")}
    document = state["document"]
    local = state["extracted_fields"]
    boxes: dict[int, list[list[int]]] = {}
    for field in ID_FIELDS:
        if field in local and local[field].get("box"):
            boxes.setdefault(local[field]["page"], []).append(local[field]["box"])
    try:
        pages = read_document_in_cloud(document["file_path"], document["mime_type"], document["pages"], boxes, key,
                                       engine=engine)
    except (CloudOcrError, OcrError) as exc:
        return {**done, "warnings": [*warnings, f"Cloud OCR unavailable ({exc})"],
                "events": add_event(state, "cloud_ocr:failed")}
    pages = [clean_page(page) for page in pages]
    found = extract_fields_from_pages(pages, state["document_type"])
    found.update(extract_lists(pages, state["document_type"]))
    found.update(heading_fields(pages, state["document_type"], found))
    fields = {name: dict(item) for name, item in local.items()}
    replaced = 0
    for name, item in found.items():
        if name in ID_FIELDS:
            continue  # blacked out before sending; the local reading is kept
        mine = fields.get(name)
        mine_ok = bool(mine and not mine.get("layout") and mine.get("confidence", 0) >= CONFIDENCE_THRESHOLD
                       and not is_blank(normalize_field(name, mine["value"]))
                       and not free_text_problems(name, mine["value"]))
        cloud_ok = not is_blank(normalize_field(name, item["value"])) and not free_text_problems(name, item["value"])
        if not mine_ok and cloud_ok:
            fields[name] = {**item, "source_ref": f"cloud:{item['source_ref']}", "source": "cloud_ocr",
                            "cloud_engine": ENGINES[engine][0]}
            replaced += 1
    return {
        **done,
        "document": {**document, "cloud_pages": pages},
        "extracted_fields": fields,
        "events": add_event(state, f"cloud_ocr:replaced_{replaced}"),
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
        # A list (tests, medicines, items) is corrected as a whole list of rows.
        confirmed[key] = normalize_field(key, raw_value if isinstance(raw_value, list) else str(raw_value))
    missing = [key for key in REQUIRED_FIELDS[state["document_type"]] if is_blank(confirmed.get(key))]
    if missing:
        return {
            "status": "needs_review",
            "route": "stop",
            "errors": [f"Correct these required fields: {', '.join(sorted(missing))}"],
            "events": add_event(state, "human_field_review:missing_fields"),
        }
    today = date.fromisoformat(state["document"].get("as_of", date.today().isoformat()))
    for key, label in FUTURE_DATE_LABELS.items():
        if confirmed.get(key) and date.fromisoformat(confirmed[key]) > today:
            return {
                "status": "needs_review",
                "route": "stop",
                "errors": [f"{label} cannot be in the future"],
                "events": add_event(state, "human_field_review:future_issue_date" if key == "issue_date"
                                    else "human_field_review:future_date"),
            }
    return {
        "normalized_fields": confirmed,
        "review_decision": decision,
        "status": "reviewed",
        "route": "continue",
        "events": add_event(state, "human_field_review:confirmed"),
    }


def mask_pages(document: dict[str, Any], replacements: dict[str, str]) -> dict[str, Any]:
    """A copy of the document whose page text has no full ID numbers left.

    First every known spelling of an extracted ID (replacements), then any other ID
    number in the text, such as an Aadhaar number printed with no label. OCR line
    "parts" (pieces used only to find fields) are dropped: a number split across two
    pieces could not be masked reliably piece by piece.
    """
    def mask_text(text: str) -> str:
        for original in sorted(replacements, key=len, reverse=True):
            text = text.replace(original, replacements[original])
        return mask_ids_in_text(text)

    def mask_page(page: dict[str, Any]) -> dict[str, Any]:
        masked = {**page, "text": mask_text(page.get("text", ""))}
        if "lines" in page:
            masked["lines"] = [{**{k: v for k, v in line.items() if k != "parts"}, "text": mask_text(line["text"])}
                               for line in page["lines"]]
        return masked

    masked_document = {**document, "pages": [mask_page(page) for page in document.get("pages", [])]}
    if "cloud_pages" in document:  # the second reading in the cloud, if one was made
        masked_document["cloud_pages"] = [mask_page(page) for page in document["cloud_pages"]]
    return masked_document


def protect_identifiers(state: DocumentState) -> dict[str, Any]:
    # Runs after review, so the reviewer could compare the full number with the card.
    fields = state["normalized_fields"]
    present = [key for key in ID_FIELDS if fields.get(key)]
    if not present:
        # No ID field was extracted, but the page may still show one (e.g. an unlabelled number).
        return {
            "document": mask_pages(state["document"], {}),
            "route": "continue",
            "events": add_event(state, "protect_identifiers:none"),
        }
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
    # The OCR page text is later stored as searchable chunks, so mask it too.
    document = mask_pages(state["document"], replacements)
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


def mask_on_stop(state: DocumentState) -> dict[str, Any]:
    """Every run that stops early ends here (Decision #27).

    A review pause (needs_review) keeps the full numbers, so the owner can compare them with
    the card. Every other stop (rejected, needs_reupload, needs_classification, failed_*) has
    no reason to keep them: ID fields, the owner's edits and all page text are masked. This
    needs no hash key, because nothing is stored.
    """
    if state.get("status") == "needs_review":
        return {"events": add_event(state, "mask_on_stop:kept_for_review")}
    extracted = {name: dict(item) for name, item in state.get("extracted_fields", {}).items()}
    normalized = dict(state.get("normalized_fields", {}))
    decision = dict(state.get("review_decision") or {})
    edits = dict(decision.get("edits", {}))
    replacements = {}  # every spelling of a full number -> its mask

    def masked(field: str, value: Any) -> Any:
        if is_blank(value) or not isinstance(value, str | int):
            return value
        mask = mask_identifier(field, str(value))
        replacements[str(value)] = mask
        return mask

    for field in ID_FIELDS:
        if field in extracted:
            for part in ("value", "ocr_value"):
                if part in extracted[field]:
                    extracted[field][part] = masked(field, extracted[field][part])
        if field in normalized:
            normalized[field] = masked(field, normalized[field])
        if field in edits:
            edits[field] = masked(field, edits[field])
    update: dict[str, Any] = {"events": add_event(state, "mask_on_stop:masked")}
    if state.get("document"):
        update["document"] = mask_pages(state["document"], replacements)
    if "extracted_fields" in state:
        update["extracted_fields"] = extracted
    if "normalized_fields" in state:
        update["normalized_fields"] = normalized
    if state.get("review_decision"):
        update["review_decision"] = {**decision, **({"edits": edits} if "edits" in decision else {})}
    return update


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
    """checkpointer: needed only for real pauses (use_interrupt). It must encrypt what it saves,
    so use utils.checkpoints.make_checkpointer(); a plain one is refused."""
    if checkpointer is not None and not is_encrypted(checkpointer):
        raise ValueError("The document graph saves personal data; use utils.checkpoints.make_checkpointer()")
    install_safe_client()  # if LangSmith tracing is on, only masked data is sent
    graph = StateGraph(DocumentState)
    graph.add_node("validate_upload", validate_upload)
    graph.add_node("check_file", check_file)
    graph.add_node("read_file", read_file)
    graph.add_node("classify_document", classify_document)
    graph.add_node("extract_content", extract_content)
    graph.add_node("llm_extract", llm_extract)
    graph.add_node("validate_candidates", validate_candidates)
    graph.add_node("confidence_gate", confidence_gate)
    graph.add_node("human_field_review", human_field_review)
    graph.add_node("protect_identifiers", protect_identifiers)
    graph.add_node("publish_confirmed_version", publish_confirmed_version)

    graph.add_edge(START, "validate_upload")
    graph.add_conditional_edges("validate_upload", route_step, {"continue": "check_file", "stop": "mask_on_stop"})
    graph.add_conditional_edges("check_file", route_step, {"continue": "read_file", "stop": "mask_on_stop"})
    graph.add_conditional_edges("read_file", route_step, {
        "continue": "classify_document", "retry": "read_file", "stop": "mask_on_stop",
    })
    graph.add_conditional_edges("classify_document", route_step, {"continue": "extract_content", "stop": "mask_on_stop"})
    graph.add_conditional_edges("extract_content", route_step, {
        "continue": "validate_candidates", "retry": "extract_content", "llm": "llm_extract", "stop": "mask_on_stop",
    })
    graph.add_conditional_edges("llm_extract", route_step, {"continue": "validate_candidates", "stop": "mask_on_stop"})
    graph.add_node("cloud_ocr", cloud_ocr)
    graph.add_conditional_edges("validate_candidates", route_step, {
        "continue": "confidence_gate", "cloud_ocr": "cloud_ocr",
    })
    graph.add_edge("cloud_ocr", "validate_candidates")
    graph.add_conditional_edges("confidence_gate", route_step, {
        "auto_accept": "protect_identifiers", "review": "human_field_review",
    })
    graph.add_conditional_edges("human_field_review", route_step, {
        "continue": "protect_identifiers", "stop": "mask_on_stop",
    })
    graph.add_conditional_edges("protect_identifiers", route_step, {
        "continue": "publish_confirmed_version", "stop": "mask_on_stop",
    })
    graph.add_edge("publish_confirmed_version", END)
    graph.add_node("mask_on_stop", mask_on_stop)  # every early stop ends here (Decision #27)
    graph.add_edge("mask_on_stop", END)

    return graph.compile(checkpointer=checkpointer)


# Plain invoke(initial_state) works when review_decision is already supplied.
if __name__ == '__main__':
    document_graph = build_document_graph()
    if not os.path.exists('doc_graph.png'):
        from IPython.display import display, Image
        img = Image(document_graph.get_graph().draw_mermaid_png())
        with open('doc_graph.png', 'wb') as file:
            file.write(img.data)
