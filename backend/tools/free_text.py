"""Checks for free-text organization names that OCR read with high confidence but wrongly.

Found on the hard set (Decision #26): glare cut "Medical Board, Demo District Hospital"
to "Medical Board, Demo D", and a worn page gave "Distriet", both at 0.97 confidence.
Free text has no format to check, so two narrow signs are used:
- **cut off:** the value ends in a lone letter or a joining word or mark ("of", "&", ",");
- **near miss:** a word is not in the word list below but is one or two letters away from
  a word in it ("Distriet" -> "district"). Words far from every listed word (place names,
  brand names) are not flagged: a misread place name is left to the owner's review.

A problem only adds a review flag; nothing is changed. Flags never quote the value, because
review flags are sent to tracing.
"""

from __future__ import annotations

import re

from .demo_tools import INDIAN_STATES

FREE_TEXT_FIELDS = ("issuing_authority", "insurer", "bank_name", "lab_name", "provider")

ORGANIZATION_WORDS = set("""
office district collector collectorate tahsildar tehsildar taluka taluk tehsil sub divisional division
magistrate medical board hospital hospitals government govt department revenue social welfare justice
empowerment municipal corporation council commissioner commission chief civil surgeon primary health centre
center community general insurance assurance company limited ltd private public sector bank banking
cooperative operative national state india indian central regional rural urban gramin grameen finance
financial life diagnostics diagnostic pathology laboratory laboratories lab labs clinic clinics clinical
research institute institution medicine medicines pharmacy pharma pharmaceuticals chemist chemists druggist
druggists nursing home care healthcare multispeciality multispecialty speciality specialty super memorial
trust charitable city demo sample authority registrar certifying issuing officer panchayat gram village block
zilla zila parishad mandal nagar palika union territory ministry family services service mutual people peoples
citizens senior first united oriental new star allied sahakari credit society scan scans imaging radiology
centre centres medical college university association federation foundation mission welfare scheme schemes
directorate director deputy assistant joint secretary superintendent dispensary unit wing branch main head
north south east west new old and the for of
""".split())
ORGANIZATION_WORDS |= {word for name in INDIAN_STATES for word in name.split()}

JOINING_WORDS = {"of", "and", "the", "for", "&"}
TRAILING_MARKS = (",", "-", "&", "/", "(")
WORD = re.compile(r"[A-Za-z]+")


def _distance(a: str, b: str, limit: int) -> int:
    """Edit distance between a and b, stopping early once it is above limit."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        current = [i]
        for j, char_b in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (char_a != char_b)))
        if min(current) > limit:
            return limit + 1
        previous = current
    return previous[-1]


def near_miss(word: str) -> str | None:
    """The listed word this one is probably a misreading of, or None."""
    lower = word.lower()
    if len(lower) < 5 or lower in ORGANIZATION_WORDS or lower.rstrip("s") in ORGANIZATION_WORDS:
        return None
    limit = 1 if len(lower) < 8 else 2
    for known in ORGANIZATION_WORDS:
        if len(known) >= 5 and _distance(lower, known, limit) <= limit:
            return known
    return None


def looks_cut_off(value: str) -> bool:
    text = value.strip()
    if not text:
        return False
    if text.endswith(TRAILING_MARKS):
        return True
    last = text.split()[-1].rstrip(".").lower()
    return last in JOINING_WORDS or (len(last) == 1 and last.isalpha())


def free_text_problems(key: str, value: object) -> list[str]:
    """Review notes for an organization name. Empty when nothing looks wrong."""
    if key not in FREE_TEXT_FIELDS or not isinstance(value, str):
        return []
    problems = []
    if looks_cut_off(value):
        problems.append("may be cut off")
    if any(near_miss(word) for word in WORD.findall(value)):
        problems.append("a word may be misread")
    return problems
