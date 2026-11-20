"""Documents read by a vision language model (Groq), straight into fields.

There is no reader on this machine for photos and scanned pages: with the senior's consent the
vision model reads every one of them, of every type (Decision #41; all types since
full-test-review Decision 14; the only reader since the vision-only session). What keeps it safe:
- faces and QR codes are painted black before the picture leaves the machine (an ID card's
  QR code holds personal data); ID numbers can no longer be found on the picture, so they are
  sent, and the model reads them like any other field (vision-only Decision 1);
- every value the model gives is reviewed by the owner (the model gives no confidence
  and can make up a plausible value, e.g. a wrong dose);
- an ID number read by the model is checked (format, check digit) and masked and hashed
  before anything is saved, like one the owner typed in;
- where a PDF also stores text and that text has the same field read differently, the owner
  is told; where the stored text does not contain the model's value at all, the owner is told too.

Values other than the ID fields asked for never include ID numbers: any that come back are masked.
"""

from __future__ import annotations

import base64
import io
import json
import re
from typing import Any

import httpx
import numpy as np

from ..utils.privacy import ID_FIELDS, mask_ids_in_text
from .cloud_ocr import (
    GROQ_CHAT_URL,
    GROQ_TIMEOUT_SECONDS,
    THINKING,
    CloudOcrError,
    _shrink,
    black_out,
    face_boxes,
    groq_model,
    post_with_retry,
    qr_boxes,
)
from .ocr import page_images

MAX_PAGES = 3  # images per request the Groq vision model accepts

SHAPES: dict[str, dict[str, Any]] = {
    "prescription": {
        "name": "the patient's name",
        "doctor_name": "the doctor's name, with Dr. if printed",
        "prescription_date": "the date of the prescription",
        "medicines": [{"name": "medicine name", "form": "tablet, capsule, syrup...", "strength": "e.g. 500 mg",
                       "frequency": "e.g. 1-0-1 or twice daily", "duration": "e.g. 5 days"}],
    },
    "lab_report": {
        "name": "the patient's name",
        "lab_name": "the laboratory's name",
        "report_date": "the report date",
        "tests": [{"name": "test name", "value": "result", "unit": "unit", "ref_range": "reference range"}],
    },
    "medical_bill": {
        "name": "the patient's name",
        "provider": "the hospital, clinic or pharmacy name",
        "bill_date": "the bill date",
        "total_amount": "the total amount payable",
        "items": [{"description": "item or service", "amount": "its amount"}],
    },
    "identity": {
        "name": "the card holder's name in English letters",
        "date_of_birth": "the date of birth",
        "aadhaar_number": "the 12-digit Aadhaar number, if this is an Aadhaar card (not the Virtual ID)",
        "pan_number": "the 10-character PAN, if this is a PAN card",
    },
    "income_certificate": {
        "name": "the name of the person the certificate is about",
        "annual_income": "the yearly income amount, with Rs. or the rupee sign if printed",
        "issue_date": "the date the certificate was issued",
        "issuing_authority": "the office or officer that issued it",
        "expiry_date": "the date it is valid until, if printed",
    },
    "residence_proof": {
        "name": "the name of the person the certificate is about",
        "residence_state": "the Indian state the person lives in",
        "issue_date": "the date the certificate was issued",
        "issuing_authority": "the office or officer that issued it",
        "expiry_date": "the date it is valid until, if printed",
    },
    "health_insurance": {
        "name": "the insured person's name",
        "insurer": "the insurance company's name",
        "expiry_date": "the date the cover ends",
        "sum_insured": "the sum insured (cover amount)",
        "policy_number": "the policy number",
    },
    "bank_passbook": {
        "name": "the account holder's name",
        "bank_name": "the bank's name",
        "ifsc": "the IFSC code of the branch",
        "account_number": "the account number",
    },
    "disability_certificate": {
        "name": "the name of the person the certificate is about",
        "issuing_authority": "the medical board or authority that issued it",
        "issue_date": "the date the certificate was issued",
        "disability_percent": "the percentage of disability",
        "expiry_date": "the date it is valid until, if printed",
    },
}
LABELS = {"prescription": "a prescription", "lab_report": "a lab report", "medical_bill": "a medical bill",
          "identity": "an identity card", "income_certificate": "an income certificate",
          "residence_proof": "a residence (domicile) certificate", "health_insurance": "a health insurance card or policy",
          "bank_passbook": "a bank passbook", "disability_certificate": "a disability certificate"}

ABHA_NUMBER = re.compile(r"(?<!\d)\d{2}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}(?!\d)")
ABHA_ADDRESS = re.compile(r"\b[\w.]+@(?:abdm|sbx)\b", re.IGNORECASE)


def prompt_for(document_type: str) -> str:
    shape = json.dumps(SHAPES[document_type], indent=1)
    return (
        f"These pictures are pages of {LABELS[document_type]} from India. Some areas are painted black on "
        "purpose; never guess what is under them. Return only one JSON object shaped like this example, "
        f"with the descriptions replaced by what is on the page:\n{shape}\n"
        "Copy text exactly as printed or handwritten: do not correct spelling, expand abbreviations or "
        "convert units. Use null for anything that is not visible or that you are not sure of; an empty "
        "list if there are no rows. Never include an ID number (Aadhaar, Virtual ID, PAN, ABHA health ID, "
        "policy or account number) except in a field above that asks for it."
    )


def classify_prompt() -> str:
    choices = "\n".join(f'- "{document_type}": {label}' for document_type, label in LABELS.items())
    return (
        "These pictures are pages of one document from India. Some areas are painted black on purpose. "
        "Which one of these is it?\n"
        f"{choices}\n"
        'Return only one JSON object: {"document_type": "<one of the names above>"}, '
        'or {"document_type": null} if it is none of them or you are not sure.'
    )


HEALTH_ID_MASK = "[health ID hidden]"


def mask_health_ids(text: str) -> str:
    return ABHA_ADDRESS.sub(HEALTH_ID_MASK, ABHA_NUMBER.sub(HEALTH_ID_MASK, text))


def _text(value: Any, masked: bool = True) -> str | None:
    """One value as plain text. masked=False only for an ID field that was asked for: the graph
    checks, masks and hashes it later, like a number the owner typed in."""
    if value is None or isinstance(value, (dict, list)):
        return None
    text = " ".join(str(value).split())
    if not text or text.casefold() in {"null", "none", "n/a", "-"}:
        return None
    return mask_health_ids(mask_ids_in_text(text)) if masked else text


def _json_object(answer: str) -> dict[str, Any]:
    """The one JSON object in the model's reply (reasoning and words around it are dropped)."""
    text = THINKING.sub("", answer)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise CloudOcrError("The vision model sent an answer that could not be read")
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        raise CloudOcrError("The vision model sent an answer that could not be read") from None
    if not isinstance(data, dict):
        raise CloudOcrError("The vision model sent an answer that could not be read")
    return data


def fields_from_answer(answer: str, document_type: str) -> dict[str, Any]:
    """The model's JSON -> {field: text or list of rows}. Unknown keys and empty values are dropped."""
    data = _json_object(answer)
    fields: dict[str, Any] = {}
    for key, example in SHAPES[document_type].items():
        value = data.get(key)
        if isinstance(example, list):
            columns = example[0].keys()
            rows = []
            for row in value if isinstance(value, list) else []:
                if isinstance(row, dict):
                    clean = {column: _text(row.get(column)) for column in columns}
                    if any(clean.values()):
                        rows.append(clean)
            if rows:
                fields[key] = rows
        elif (clean := _text(value, masked=key not in ID_FIELDS)) is not None:
            fields[key] = clean
    return fields


def type_from_answer(answer: str) -> str | None:
    """The model's {"document_type": ...} -> one of our types, or None if it named none of them."""
    named = _json_object(answer).get("document_type")
    return named if isinstance(named, str) and named in SHAPES else None


def safe_page_images(path: str, mime_type: str) -> list[np.ndarray]:
    """Every page with QR codes and faces painted black."""
    return [black_out(image, qr_boxes(image) + face_boxes(image)) for image in page_images(path, mime_type)]


def picture_content(path: str, mime_type: str) -> list[dict[str, Any]]:
    """The pages as JPEG pictures for one request. More than MAX_PAGES is refused before anything is sent."""
    images = safe_page_images(path, mime_type)
    if len(images) > MAX_PAGES:
        raise CloudOcrError(f"more than {MAX_PAGES} pages")
    content = []
    for image in images:
        buffer = io.BytesIO()
        _shrink(image).convert("RGB").save(buffer, format="JPEG", quality=90)
        picture = base64.b64encode(buffer.getvalue()).decode("ascii")
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{picture}"}})
    return content


def ask_vision_model(content: list[dict[str, Any]], api_key: str, client: httpx.Client | None = None,
                     model: str | None = None) -> str:
    """Send one prompt and its pictures; return the model's reply text."""
    # JSON mode: Groq makes the reply one valid JSON object (the prompt gives its shape and must say
    # "JSON"), or answers 400 instead. full-test-review 14d: same fields right as plain text, valid
    # JSON every time. The reply is still parsed as before.
    body = {"model": model or groq_model(), "temperature": 0, "max_completion_tokens": 4096,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": content}]}
    own_client = client is None
    client = client or httpx.Client(timeout=GROQ_TIMEOUT_SECONDS)
    try:
        response = post_with_retry(client, GROQ_CHAT_URL, "groq_vision", json=body,
                                   headers={"Authorization": f"Bearer {api_key}"})
    except httpx.HTTPError as exc:
        raise CloudOcrError(f"the vision model could not be reached ({type(exc).__name__})") from exc
    finally:
        if own_client:
            client.close()
    if response.status_code != 200:
        raise CloudOcrError(f"the vision model failed (HTTP {response.status_code})")  # body not shown
    try:
        answer = response.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise CloudOcrError("The vision model sent an answer that could not be read") from None
    if not isinstance(answer, str):
        raise CloudOcrError("The vision model sent an answer that could not be read")
    return answer


def extract_with_vision(path: str, mime_type: str, document_type: str, api_key: str,
                        client: httpx.Client | None = None, model: str | None = None) -> dict[str, Any]:
    """Every field of this type the model can read on the pages (its ID fields too)."""
    if document_type not in SHAPES:
        raise CloudOcrError("The vision model does not read this document type")
    content = [{"type": "text", "text": prompt_for(document_type)}, *picture_content(path, mime_type)]
    return fields_from_answer(ask_vision_model(content, api_key, client, model), document_type)


def classify_with_vision(path: str, mime_type: str, api_key: str, client: httpx.Client | None = None,
                         model: str | None = None) -> str | None:
    """Which of our document types the pictures show, or None if the model can't tell."""
    content = [{"type": "text", "text": classify_prompt()}, *picture_content(path, mime_type)]
    return type_from_answer(ask_vision_model(content, api_key, client, model))
