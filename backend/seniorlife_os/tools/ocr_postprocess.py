"""Clean OCR text before fields are read from it (plan step 7c-2).

Only changes the *form* of characters, never the meaning of a value:
  - "nfkc":     Unicode NFKC (full-width "／" -> "/", "：" -> ":", odd spaces -> " ")
  - "invisible": zero-width space / byte-order mark removed
  - "dash":     ‐ ‑ ‒ – — ― − -> "-"
  - "quote":    ‘ ’ ‚ ‛ -> ' and “ ” „ -> "
  - "currency": "Rs.", "Rs", "INR", "₨" written before an amount -> "₹"
  - "spaces":   runs of spaces -> one space

Each changed line records which rules fired in line["cleanup"] (rule names only,
never the old text, so no extra copy of an ID number is kept).

fix_lookalikes (step 7c-4) is separate: it can change a value (O -> 0 in an IFSC),
so the graph only uses it when the value fails validation as read, only keeps it if
the fixed value passes, and always sends the field to review.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from .demo_tools import MONTH_NUMBERS, normalize_field

INVISIBLE = re.compile("[​﻿]")  # zero-width joiners are kept: Hindi needs them
DASHES = re.compile("[‐‑‒–—―−]")
SINGLE_QUOTES = re.compile("[‘’‚‛]")
DOUBLE_QUOTES = re.compile("[“”„]")
# A currency word only counts when an amount follows; "Mrs." or "Hrs" never match.
CURRENCY = re.compile(r"(?<![A-Za-z])(?:Rs\.?|INR|₨)\s*(?=\d)", re.IGNORECASE)
SPACES = re.compile(r"[ \t]{2,}")

RULES = (
    ("nfkc", lambda text: unicodedata.normalize("NFKC", text)),
    ("invisible", lambda text: INVISIBLE.sub("", text)),
    ("dash", lambda text: DASHES.sub("-", text)),
    ("quote", lambda text: DOUBLE_QUOTES.sub('"', SINGLE_QUOTES.sub("'", text))),
    ("currency", lambda text: CURRENCY.sub("₹", text)),
    ("spaces", lambda text: SPACES.sub(" ", text).strip()),
)


# ---------- look-alike characters inside values (step 7c-4) ----------

# Characters OCR confuses, by what the position needs to be.
AS_DIGIT = {"O": "0", "o": "0", "D": "0", "Q": "0", "I": "1", "l": "1", "i": "1", "|": "1", "!": "1",
            "Z": "2", "z": "2", "S": "5", "s": "5", "B": "8", "G": "6", "b": "6", "T": "7", "g": "9", "q": "9"}
AS_LETTER = {"0": "O", "1": "I", "2": "Z", "5": "S", "8": "B", "6": "G", "7": "T", "4": "A"}
CURRENCY_PREFIX = re.compile(r"^(₹|Rs\.?|INR)\s*", re.IGNORECASE)


def _swap(text: str, want_digit: str) -> tuple[str, list[str]]:
    """want_digit[i] is 'd' (digit), 'L' (letter) or '.' (leave alone) for each character."""
    out, changes = [], []
    for char, want in zip(text, want_digit):
        new = AS_DIGIT.get(char, char) if want == "d" else AS_LETTER.get(char, char) if want == "L" else char
        if new != char:
            changes.append(f"{char}→{new}")
        out.append(new)
    return "".join(out), changes


def _digits_everywhere(text: str, keep: str = " -,/.%") -> tuple[str, list[str]]:
    return _swap(text, "".join("." if c in keep else "d" for c in text))


def fix_lookalikes(key: str, raw_value: str) -> tuple[str, list[str]] | None:
    """Swap look-alike characters where the field's shape demands a digit or a letter.

    Returns (fixed text, ["O→0", ...]) or None if this field has no fixed shape or nothing
    changed. The caller must check the fixed text still passes the field's own validation
    and must send the field to review: this is a suggestion, not a fact.
    """
    value = raw_value.strip()
    if key in {"aadhaar_number", "account_number", "disability_percent"}:
        fixed, changes = _digits_everywhere(value)
    elif key == "pan_number" and len(value) == 10:  # AAAAA9999A
        fixed, changes = _swap(value, "LLLLLddddL")
    elif key == "ifsc" and len(value) == 11:  # AAAA0XXXXXX: last six may be letters or digits
        fixed, changes = _swap(value, "LLLLd......")
    elif key in {"annual_income", "sum_insured", "total_amount"}:
        prefix = CURRENCY_PREFIX.match(value)
        head = prefix.group(0) if prefix else ""
        body = value[len(head):]
        tail = "/-" if body.endswith("/-") else ""
        number, changes = _digits_everywhere(body[: len(body) - len(tail)])
        fixed = head + number + tail
    elif key in {"issue_date", "expiry_date", "date_of_birth", "report_date", "prescription_date", "bill_date"}:
        # Leave a month name ("Aug") alone; everything else must be digits or separators.
        parts, changes = [], []
        for token in re.split(r"([/.\- ,]+)", value):
            if token.lower() in MONTH_NUMBERS or re.fullmatch(r"[/.\- ,]*", token):
                parts.append(token)  # a month name or a separator
                continue
            fixed_token, token_changes = _digits_everywhere(token)
            parts.append(fixed_token)
            changes += token_changes
        fixed = "".join(parts)
    else:
        return None  # names, policy numbers, free text: no fixed shape to lean on
    return (fixed, changes) if changes else None


# Fields with a strict shape: text after the value (a stamp, the next column) can be cut off.
TRIMMABLE = {"issue_date", "expiry_date", "date_of_birth", "report_date", "prescription_date", "bill_date",
             "annual_income", "sum_insured", "total_amount", "aadhaar_number", "pan_number", "account_number",
             "ifsc", "disability_percent"}


def repair_value(key: str, raw_value: str) -> tuple[str, Any, list[str]] | None:
    """Try to make a value that fails validation pass, without guessing.

    In order: look-alike swaps on the whole value; then, for strictly shaped fields,
    the longest run of leading words that passes (with look-alike swaps if needed),
    e.g. "23/12/2026 DEMO TALUKA OFFICE" -> "23/12/2026".
    Returns (text used, normalized value, notes for the reviewer) or None.
    """
    def attempt(text: str) -> tuple[str, Any, list[str]] | None:
        value = normalize_field(key, text)
        if value is not None:
            return text, value, []
        fix = fix_lookalikes(key, text)
        value = normalize_field(key, fix[0]) if fix else None
        if value is not None:
            return fix[0], value, [f"look-alike characters corrected ({', '.join(fix[1])})"]
        return None

    whole = attempt(raw_value.strip())
    if whole:
        return whole if whole[2] else None  # no notes: the value was already fine, nothing repaired
    if key in TRIMMABLE:
        words = raw_value.split()
        for count in range(len(words) - 1, 0, -1):
            found = attempt(" ".join(words[:count]))
            if found:
                return found[0], found[1], [*found[2], "extra text after the value was ignored"]
    return None


def lookalike_key(text: str) -> str:
    """Text with I/l/1/| and O/0 folded together, for 'same except look-alikes' checks."""
    return text.casefold().translate(str.maketrans({"l": "i", "1": "i", "|": "i", "0": "o"}))


def clean_line(text: str) -> tuple[str, list[str]]:
    """Return the cleaned line and the names of the rules that changed it."""
    fired = []
    for name, rule in RULES:
        cleaned = rule(text)
        if cleaned != text:
            fired.append(name)
            text = cleaned
    return text, fired


def clean_page(page: dict[str, Any]) -> dict[str, Any]:
    """A cleaned copy of one page from tools/ocr.py (the input is not changed)."""
    if "lines" in page:
        lines = []
        for line in page["lines"]:
            text, fired = clean_line(line["text"])
            if not text:
                continue  # keep "text" and "lines" one-to-one
            cleaned = {**line, "text": text}
            if fired:
                cleaned["cleanup"] = fired
            if "parts" in line:
                parts = [{**part, "text": clean_line(part["text"])[0]} for part in line["parts"]]
                cleaned["parts"] = [part for part in parts if part["text"]]
            lines.append(cleaned)
        return {**page, "lines": lines, "text": "\n".join(line["text"] for line in lines)}
    return {**page, "text": "\n".join(clean_line(text)[0] for text in page.get("text", "").splitlines())}
