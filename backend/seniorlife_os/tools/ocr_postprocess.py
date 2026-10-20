"""Clean OCR text before fields are read from it (plan step 7c-2).

Only changes the *form* of characters, never the meaning of a value:
  - "nfkc":     Unicode NFKC (full-width "／" -> "/", "：" -> ":", odd spaces -> " ")
  - "invisible": zero-width space / byte-order mark removed
  - "dash":     ‐ ‑ ‒ – — ― − -> "-"
  - "quote":    ‘ ’ ‚ ‛ -> ' and “ ” „ -> "
  - "currency": "Rs.", "Rs", "INR", "₨" written before an amount -> "₹"
  - "spaces":   runs of spaces -> one space

Each changed line records which rules fired in line["cleanup"] (rule names only,
never the old text, so no extra copy of an ID number is kept). Fixing look-alike
characters inside values (O -> 0 and so on) is a separate, reviewed step (7c-4).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

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
            lines.append(cleaned)
        return {**page, "lines": lines, "text": "\n".join(line["text"] for line in lines)}
    return {**page, "text": "\n".join(clean_line(text)[0] for text in page.get("text", "").splitlines())}
