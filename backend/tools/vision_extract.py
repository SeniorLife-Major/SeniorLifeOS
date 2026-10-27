"""Medical documents read by a vision language model (Groq), straight into fields.

For prescriptions, lab reports and medical bills a vision model reads handwriting and
free layouts far better than local OCR, so with the senior's consent it is the main
reader for these types (Decision #41). It is never the only step:
- local OCR always runs first, to find where ID numbers (Aadhaar, PAN, account and
  policy numbers, ABHA health IDs) and QR codes are, so they are painted black before
  the picture leaves the machine;
- every value the model gives is reviewed by the owner (the model gives no confidence
  and can make up a plausible value, e.g. a wrong dose);
- where the local reading has the same field and it differs, the owner is told.

Values never include ID numbers: any that come back are masked.
"""

from __future__ import annotations

import base64
import io
import json
import re
from typing import Any

import httpx
import numpy as np

from ..utils.privacy import mask_ids_in_text
from .cloud_ocr import (
    GROQ_CHAT_URL,
    GROQ_TIMEOUT_SECONDS,
    THINKING,
    CloudOcrError,
    _shrink,
    black_out,
    groq_model,
    id_boxes,
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
}
LABELS = {"prescription": "a prescription", "lab_report": "a lab report", "medical_bill": "a medical bill"}

ABHA_NUMBER = re.compile(r"(?<!\d)\d{2}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}(?!\d)")
ABHA_ADDRESS = re.compile(r"\b[\w.]+@(?:abdm|sbx)\b", re.IGNORECASE)
ABHA_LABEL = re.compile(r"\b(?:abha|health\s*id)\b", re.IGNORECASE)


def prompt_for(document_type: str) -> str:
    shape = json.dumps(SHAPES[document_type], indent=1)
    return (
        f"These pictures are pages of {LABELS[document_type]} from India. Some areas are painted black on "
        "purpose; never guess what is under them. Return only one JSON object shaped like this example, "
        f"with the descriptions replaced by what is on the page:\n{shape}\n"
        "Copy text exactly as printed or handwritten: do not correct spelling, expand abbreviations or "
        "convert units. Use null for anything that is not visible or that you are not sure of; an empty "
        "list if there are no rows. Never include ID numbers (Aadhaar, PAN, ABHA, policy or account numbers)."
    )


def health_id_boxes(page: dict[str, Any]) -> list[list[int]]:
    """Boxes of OCR lines that show an ABHA health ID (number or address) or its label."""
    return [line["box"] for line in page.get("lines", [])
            if line.get("box") and (ABHA_NUMBER.search(line["text"]) or ABHA_ADDRESS.search(line["text"])
                                    or ABHA_LABEL.search(line["text"]))]


HEALTH_ID_MASK = "[health ID hidden]"


def mask_health_ids(text: str) -> str:
    return ABHA_ADDRESS.sub(HEALTH_ID_MASK, ABHA_NUMBER.sub(HEALTH_ID_MASK, text))


def _text(value: Any) -> str | None:
    if value is None or isinstance(value, (dict, list)):
        return None
    text = " ".join(str(value).split())
    if not text or text.casefold() in {"null", "none", "n/a", "-"}:
        return None
    return mask_health_ids(mask_ids_in_text(text))


def fields_from_answer(answer: str, document_type: str) -> dict[str, Any]:
    """The model's JSON -> {field: text or list of rows}. Unknown keys and empty values are dropped."""
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
        elif (clean := _text(value)) is not None:
            fields[key] = clean
    return fields


def safe_page_images(path: str, mime_type: str, local_pages: list[dict[str, Any]],
                     id_field_boxes: dict[int, list[list[int]]]) -> list[np.ndarray]:
    """Every page with ID numbers, health IDs and QR codes painted black."""
    images = []
    for local, image in zip(local_pages, page_images(path, mime_type)):
        boxes = id_boxes(local, id_field_boxes.get(local["page"], [])) + health_id_boxes(local)
        images.append(black_out(image, boxes + qr_boxes(image)))
    return images


def extract_with_vision(path: str, mime_type: str, local_pages: list[dict[str, Any]],
                        id_field_boxes: dict[int, list[list[int]]], document_type: str, api_key: str,
                        client: httpx.Client | None = None, model: str | None = None) -> dict[str, Any]:
    if document_type not in SHAPES:
        raise CloudOcrError("The vision model reads only prescriptions, lab reports and medical bills")
    if len(local_pages) > MAX_PAGES:
        raise CloudOcrError(f"more than {MAX_PAGES} pages")
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt_for(document_type)}]
    for image in safe_page_images(path, mime_type, local_pages, id_field_boxes):
        buffer = io.BytesIO()
        _shrink(image).convert("RGB").save(buffer, format="JPEG", quality=90)
        picture = base64.b64encode(buffer.getvalue()).decode("ascii")
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{picture}"}})
    body = {"model": model or groq_model(), "temperature": 0, "max_completion_tokens": 4096,
            "messages": [{"role": "user", "content": content}]}
    own_client = client is None
    client = client or httpx.Client(timeout=GROQ_TIMEOUT_SECONDS)
    try:
        response = client.post(GROQ_CHAT_URL, json=body, headers={"Authorization": f"Bearer {api_key}"})
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
    return fields_from_answer(answer, document_type)
