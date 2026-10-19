"""Find, validate, and hide personal identifiers before text leaves the machine.

Identifiers are swapped for placeholders such as [AADHAAR_1]. The mapping from
placeholder back to the real value stays local and is never sent anywhere.
Only the standard library is used; free-text names are hidden only when the
caller already knows them (for example the owner's name).
"""

from __future__ import annotations

import re


# Verhoeff checksum tables (used by Aadhaar's last digit).
_VERHOEFF_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
    [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
    [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_VERHOEFF_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 7, 6, 8, 0],
    [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5],
    [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]
_VERHOEFF_INV = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]

# Fourth PAN character is the holder type (P = person, C = company, ...).
PAN_HOLDER_TYPES = set("ABCFGHJLPT")

AADHAAR_PATTERN = re.compile(r"(?<!\d)[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}(?!\d)")
PAN_PATTERN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
IFSC_PATTERN = re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")
ACCOUNT_PATTERN = re.compile(r"(?i)\b(?:a/?c|account)\s*(?:no\.?|number|#)?\s*[:.\-]?\s*(\d{9,18})(?!\d)")
PHONE_PATTERN = re.compile(r"(?<![\d+])(?:\+91[ -]?)?[6-9]\d{9}(?!\d)")
EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def verhoeff_check_digit(digits: str) -> str:
    """Return the Verhoeff check digit to append to a string of digits."""
    check = 0
    for position, digit in enumerate(reversed(digits), start=1):
        check = _VERHOEFF_D[check][_VERHOEFF_P[position % 8][int(digit)]]
    return str(_VERHOEFF_INV[check])


def is_valid_aadhaar(value: str) -> bool:
    digits = re.sub(r"[ -]", "", value)
    return (
        len(digits) == 12
        and digits.isdigit()
        and digits[0] not in "01"
        and verhoeff_check_digit(digits[:-1]) == digits[-1]
    )


def is_valid_pan(value: str) -> bool:
    value = value.strip()
    return bool(PAN_PATTERN.fullmatch(value)) and value[3] in PAN_HOLDER_TYPES


def is_valid_ifsc(value: str) -> bool:
    return bool(IFSC_PATTERN.fullmatch(value.strip()))


def redact(text: str, known_values: dict[str, str] | None = None) -> tuple[str, dict[str, str]]:
    """Replace identifiers with placeholders.

    Returns the safe text and a placeholder -> original mapping. The same value
    always gets the same placeholder. IFSC codes are public branch codes and
    are left as they are.
    """
    mapping: dict[str, str] = {}
    counters: dict[str, int] = {}

    def placeholder(label: str, original: str) -> str:
        for existing, value in mapping.items():
            if value == original and existing.startswith(f"[{label}_"):
                return existing
        counters[label] = counters.get(label, 0) + 1
        token = f"[{label}_{counters[label]}]"
        mapping[token] = original
        return token

    def replace_aadhaar(match: re.Match) -> str:
        found = match.group(0)
        return placeholder("AADHAAR", found) if is_valid_aadhaar(found) else found

    def replace_pan(match: re.Match) -> str:
        found = match.group(0)
        return placeholder("PAN", found) if is_valid_pan(found) else found

    def replace_account(match: re.Match) -> str:
        # Keep the "A/c No:" label, hide only the number.
        start, end = match.start(1) - match.start(0), match.end(1) - match.start(0)
        whole = match.group(0)
        return whole[:start] + placeholder("ACCOUNT", match.group(1)) + whole[end:]

    safe = text
    for label, value in (known_values or {}).items():
        value = (value or "").strip()
        if value:
            # Any casing of a known value maps to one placeholder.
            safe = re.sub(re.escape(value), lambda _, label=label, value=value: placeholder(label, value), safe, flags=re.IGNORECASE)
    safe = AADHAAR_PATTERN.sub(replace_aadhaar, safe)
    safe = ACCOUNT_PATTERN.sub(replace_account, safe)
    safe = PHONE_PATTERN.sub(lambda m: placeholder("PHONE", m.group(0)), safe)
    safe = EMAIL_PATTERN.sub(lambda m: placeholder("EMAIL", m.group(0)), safe)
    safe = PAN_PATTERN.sub(replace_pan, safe)
    return safe, mapping
