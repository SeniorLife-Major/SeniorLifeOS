"""Find label -> value pairs in OCR lines laid out like real documents (step 7c-5).

Three layouts, each given a `lookup(label_text)` that returns a field key, STOP (a
known label that is not a field, such as "Age / Sex"), or None:

  pairs_in_line   "Patient Name : Smt. Anita Rao Age / Sex :69Y/F"   several pairs on one line
  no_colon_pair   "Name Meera Rao"                                    the colon was lost
  label_above     "Member Name      Policy Number"                     values on the next line,
                  "Meera Rao        HLT/2024/347616"                   paired by column position

The last two are guesses about layout, so the caller marks what they find for review.
"""

from __future__ import annotations

import re
from typing import Any, Callable

STOP = "__stop__"  # a label that only ends the previous value
Lookup = Callable[[str], str | None]

# A colon that is not between two digits (so "08:15" is not a label separator).
COLON = re.compile(r"\s*(?:(?<!\d):|:(?!\d))\s*")
WORD = re.compile(r"\S+")
MAX_LABEL_WORDS = 5
# A value found without a colon must start like a value, not like "of India".
VALUE_START = re.compile(r"[A-Z0-9₹]")


def label_suffix(segment: str, lookup: Lookup) -> tuple[int, str] | None:
    """The longest run of last words in `segment` that is a label: (start offset, key or STOP)."""
    words = list(WORD.finditer(segment))
    for count in range(min(MAX_LABEL_WORDS, len(words)), 0, -1):
        start = words[-count].start()
        key = lookup(segment[start:].strip())
        if key:
            return start, key
    return None


def pairs_in_line(text: str, lookup: Lookup) -> list[tuple[str, str]]:
    """Every 'Label: value' in one line. A value runs until the next known label."""
    accepted = []  # (label start, value start, key or STOP)
    segment_start = 0
    for colon in COLON.finditer(text):
        found = label_suffix(text[segment_start:colon.start()], lookup)
        if found:  # otherwise this colon is just part of a value
            offset, key = found
            accepted.append((segment_start + offset, colon.end(), key))
            segment_start = colon.end()
    pairs = []
    for index, (_, value_start, key) in enumerate(accepted):
        value_end = accepted[index + 1][0] if index + 1 < len(accepted) else len(text)
        value = text[value_start:value_end].strip(" ,;|")
        if key != STOP and value:
            pairs.append((key, value))
    return pairs


def no_colon_pair(text: str, lookup: Lookup) -> tuple[str, str] | None:
    """'Name Meera Rao' -> ('name', 'Meera Rao'). Only for lines with no colon at all."""
    if ":" in text:
        return None
    words = list(WORD.finditer(text))
    for count in range(min(MAX_LABEL_WORDS, len(words) - 1), 0, -1):
        key = lookup(text[: words[count - 1].end()])
        value = text[words[count].start():].strip()
        if key and key != STOP and VALUE_START.match(value) and not starts_with_label(value, lookup):
            return key, value
    return None


def starts_with_label(text: str, lookup: Lookup) -> bool:
    """'Policy Number HLT/1' -> True: a row of labels is not a label and its value."""
    words = list(WORD.finditer(text))
    return any(lookup(text[: words[count - 1].end()]) for count in range(1, min(MAX_LABEL_WORDS, len(words)) + 1))


def is_label_only(text: str, lookup: Lookup) -> bool:
    return ":" not in text and bool(lookup(text.strip()))


def label_above(line: dict[str, Any], next_line: dict[str, Any], lookup: Lookup) -> list[tuple[str, dict[str, Any]]]:
    """Labels on one line, values on the next. Returns [(key, value part), ...].

    With OCR pieces ("parts", each with a box) every label piece is paired with the
    value piece whose left edge is closest to its own. Without pieces, only a line
    that is one label as a whole is paired with the whole next line.
    """
    parts, next_parts = line.get("parts"), next_line.get("parts")
    if not parts or not next_parts:
        key = lookup(line["text"].strip()) if ":" not in line["text"] else None
        if key and key != STOP and not is_label_only(next_line["text"], lookup):
            return [(key, {"text": next_line["text"], "confidence": next_line.get("confidence"),
                           "box": next_line.get("box")})]
        return []
    if not all(is_label_only(part["text"], lookup) for part in parts):
        return []  # a label row holds only labels
    values = [part for part in next_parts if not is_label_only(part["text"], lookup)]
    pairs = []
    for part in parts:
        key = lookup(part["text"].strip())
        if key == STOP or not values:
            continue
        left, width = part["box"][0], part["box"][2] - part["box"][0]
        nearest = min(values, key=lambda value: abs(value["box"][0] - left))
        if abs(nearest["box"][0] - left) <= max(60, 0.6 * width):
            pairs.append((key, nearest))
    return pairs
