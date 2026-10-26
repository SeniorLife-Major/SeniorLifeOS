"""Checks for free-text organization names that OCR read with high confidence but wrongly.

Found on the hard set (Decision #26): glare cut "Medical Board, Demo District Hospital"
to "Medical Board, Demo D", and a worn page gave "Distriet", both at 0.97 confidence.
Free text has no format to check, so two narrow signs are used:
- **cut off:** the value ends in a lone letter or a joining word or mark ("of", "&", ",");
- **near miss:** a word is not a known word but is one or two letters away from one
  ("Distriet" -> "district", "Thame" -> "thane", "Bajej" -> "bajaj"). Known words are the
  organization words below plus every word of the lists in tools/data/: Indian districts
  and their headquarters towns, banks, and insurers. Words far from every known word (a
  small town, a co-operative bank) are not flagged.
- **close to an earlier document:** a name one or two letters away from the same field on
  one of the owner's earlier confirmed documents (the vault passes those names in).

A problem only adds a review flag; nothing is changed. Flags never quote the value, because
review flags are sent to tracing.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from .demo_tools import INDIAN_STATES

DATA_DIR = Path(__file__).resolve().parent / "data"
LIST_FILES = ("indian_places.txt", "indian_banks.txt", "indian_insurers.txt")

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

MIN_LISTED_WORD = 4  # shorter list words ("Goa", "SBI") are too close to too many others
JOINING_WORDS = {"of", "and", "the", "for", "&"}
TRAILING_MARKS = (",", "-", "&", "/", "(")
WORD = re.compile(r"[A-Za-z]+")


def fold(text: str) -> str:
    """Lower case, accents removed ("Mahé" -> "mahe"), so list and page words compare."""
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)).lower()


@lru_cache(maxsize=1)
def listed_words() -> frozenset[str]:
    words = {w for name in INDIAN_STATES for w in WORD.findall(fold(name)) if len(w) >= MIN_LISTED_WORD}
    for name in LIST_FILES:
        for line in (DATA_DIR / name).read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#"):
                words.update(w for w in WORD.findall(fold(line)) if len(w) >= MIN_LISTED_WORD)
    return frozenset(words)


@lru_cache(maxsize=1)
def known_words() -> frozenset[str]:
    return frozenset(ORGANIZATION_WORDS | listed_words())


def _distance(a: str, b: str, limit: int) -> int:
    """Edit distance between a and b, where swapping two neighbouring letters ("Heatlh")
    counts as one change. Stops early once it is above limit."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    before, previous = None, list(range(len(b) + 1))
    for i, char_a in enumerate(a, start=1):
        current = [i]
        for j, char_b in enumerate(b, start=1):
            cost = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (char_a != char_b))
            if before is not None and i > 1 and j > 1 and char_a == b[j - 2] and a[i - 2] == char_b:
                cost = min(cost, before[j - 2] + 1)
            current.append(cost)
        if min(current) > limit:
            return limit + 1
        before, previous = previous, current
    return previous[-1]


def near_miss(word: str) -> str | None:
    """The listed word this one is probably a misreading of, or None."""
    lower = fold(word)
    known = known_words()
    if len(lower) < 5 or lower in known or lower.rstrip("s") in known:
        return None
    # Organization words: one change (two for long words).
    limit = 1 if len(lower) < 8 else 2
    for candidate in sorted(ORGANIZATION_WORDS):  # sorted: the same answer every run
        if len(candidate) >= 5 and _distance(lower, candidate, limit) <= limit:
            return candidate
    # Listed names (places, banks, insurers) sit close together (Matheran / Mathura,
    # Durgapur / Dungarpur), so only one change, with the same first letter.
    for candidate in sorted(listed_words()):
        if len(candidate) >= 5 and candidate[0] == lower[0] and _distance(lower, candidate, 1) <= 1:
            return candidate
    return None


def looks_cut_off(value: str) -> bool:
    text = value.strip()
    if not text:
        return False
    if text.endswith(TRAILING_MARKS):
        return True
    last = text.split()[-1].rstrip(".").lower()
    return last in JOINING_WORDS or (len(last) == 1 and last.isalpha())


def comparable(name: str) -> str:
    return " ".join(WORD.findall(fold(name)))


def close_to_earlier(value: str, earlier: list[str]) -> bool:
    """One or two letters away from a name on an earlier document, but not the same."""
    mine = comparable(value)
    if len(mine) < 8:
        return False
    for name in earlier:
        theirs = comparable(name)
        if theirs and theirs != mine and _distance(mine, theirs, 2) <= 2:
            return True
    return False


def free_text_problems(key: str, value: object, earlier: list[str] | None = None) -> list[str]:
    """Review notes for an organization name. Empty when nothing looks wrong.
    earlier: this field's names on the owner's earlier confirmed documents."""
    if key not in FREE_TEXT_FIELDS or not isinstance(value, str):
        return []
    problems = []
    if looks_cut_off(value):
        problems.append("may be cut off")
    if any(near_miss(word) for word in WORD.findall(fold(value))):
        problems.append("a word may be misread")
    if earlier and close_to_earlier(value, earlier):
        problems.append("differs slightly from your earlier document")
    return problems
