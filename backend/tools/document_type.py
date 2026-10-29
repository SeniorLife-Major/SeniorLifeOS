"""Guess a document's type from the words on its page, on this machine (no model, no network).

Each type has words that Indian documents of that kind usually carry, in English and Hindi
("domicile", "पासबुक", "sum insured"). A word found in the heading (the first lines) counts
double. The type is given only when it is clearly ahead of every other type; otherwise None,
and the owner is asked to choose. Medical types also need the page to look medical, so an
electricity bill is not taken for a hospital bill.
"""

from __future__ import annotations

import re

from .medical import looks_medical, medical_type_from_text

HEADING_LINES = 3
MIN_SCORE = 4  # a strong word in the body, or a weaker one in the heading
MIN_LEAD = 2   # points ahead of the next type
MEDICAL_RULES_BONUS = 4  # the table/medicine/bill-total rules in medical.py matched

# type -> [(words, weight)]. Each entry counts once per page. Hindi has no \b: its vowel signs
# are not word characters to Python's re, so Hindi words are matched as plain text.
TYPE_WORDS: dict[str, list[tuple[str, int]]] = {
    "identity": [
        (r"\baadhaa?r\b|आधार", 2),
        (r"unique identification|\buidai\b|permanent account number|election commission|\belector\b|\bvoter\b|"
         r"driving licen[cs]e|\bpassport\b", 3),
        (r"\bidentity\b|\bidentification\b|पहचान", 2),
        (r"\bbirth\b|\bdob\b|जन्म", 1),
        (r"government of india|govt\.? of india|भारत सरकार", 1),
    ],
    "income_certificate": [
        (r"income certificate|आय प्रमाण", 4),
        (r"(?:annual|yearly|family|total) (?:income|earnings)|income from all sources|वार्षिक आय", 3),
        (r"\bincome\b|\bearnings\b", 1),
        (r"\bt[ae]h?s[ie]ldar\b|revenue department|\btalathi\b|village officer", 2),
    ],
    "residence_proof": [
        (r"\bdomicile\b|residen(?:ce|tial) certificate|proof of (?:residence|address)|certificate of address|"
         r"address proof|निवास प्रमाण|मूल निवास", 4),
        (r"\bresiden(?:t|ce|tial)\b|\bresiding\b|living at|निवासी", 2),
    ],
    "health_insurance": [
        (r"\binsurance\b|\binsured\b|\binsurer\b|mediclaim|बीमा", 3),
        (r"ayushman|jan arogya|\bpm-?jay\b", 4),
        (r"\bpolicy\b|sum (?:insured|assured)|\bpremium\b|cashless|\btpa\b", 2),
        (r"\be-?card\b|member id", 2),
    ],
    "bank_passbook": [
        (r"\bpass ?book\b|पासबुक|पास बुक", 4),
        (r"\bsavings?\b|statement of account|बचत", 2),
        (r"\bbank\b|बैंक|post office", 2),
        (r"\baccount\b|\ba/c\b|खाता", 2),
        (r"\bifsc\b|\bmicr\b|\bbranch\b|\bbalance\b|\bwithdrawals?\b|\bdeposit(?:or)?s?\b", 2),
    ],
    "disability_certificate": [
        (r"disabilit|\bdisabled\b|divyang|दिव्यांग|विकलांग|handicap", 4),
        (r"\budid\b|medical (?:board|authority)|locomotor|low vision|benchmark|impairment", 2),
    ],
    "lab_report": [
        (r"laborator|\blab\b|patholog|diagnostic|reference (?:range|interval)|normal range|specimen|"
         r"sample collected|\bprofile\b", 2),
        (r"\bmg/dl\b|\bg/dl\b|mmol/l|\btest\b|\bresults?\b", 1),
    ],
    "prescription": [
        (r"\brx\b|prescription|\bmbbs\b", 3),
        (r"\b(?:tab|cap|syp|inj)\.|\btablet\b|\bcapsule\b|\bsyrup\b", 2),
        (r"\bdr\.?\s|\bclinic\b|\bopd\b", 1),
    ],
    "medical_bill": [
        (r"\bbill\b|\binvoice\b|cash memo|\breceipt\b", 3),
        (r"\bpharmacy\b|\bhospital\b|medical store|\bchemist\b", 1),
        (r"\btotal\b|amount payable|net amount|\bgstin?\b", 1),
    ],
}
MEDICAL = {"lab_report", "prescription", "medical_bill"}
_COMPILED = {kind: [(re.compile(words, re.IGNORECASE), weight) for words, weight in entries]
             for kind, entries in TYPE_WORDS.items()}


def type_scores(text: str) -> dict[str, int]:
    """Points per document type for this page text."""
    lines = [line for line in text.splitlines() if line.strip()]
    heading, body = "\n".join(lines[:HEADING_LINES]), "\n".join(lines[HEADING_LINES:])
    medical_page, medical_rule = looks_medical(text), medical_type_from_text(text)
    scores = {}
    for kind, entries in _COMPILED.items():
        if kind in MEDICAL and not (medical_page or medical_rule == kind):
            scores[kind] = 0
            continue
        score = sum(2 * weight if words.search(heading) else weight if words.search(body) else 0
                    for words, weight in entries)
        scores[kind] = score + (MEDICAL_RULES_BONUS if medical_rule == kind else 0)
    return scores


def type_from_text(text: str) -> str | None:
    """The document type the page's words point to, or None when no type is clearly ahead."""
    ranked = sorted(type_scores(text).items(), key=lambda pair: pair[1], reverse=True)
    (best, top), (_, second) = ranked[0], ranked[1]
    return best if top >= MIN_SCORE and top - second >= MIN_LEAD else None
