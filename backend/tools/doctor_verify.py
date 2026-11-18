"""Each doctor's registration, as Practo lists it (doctor search step 2e, Decision 16).

Nothing here uses the network. Practo shows the doctor's registration number, the council that
registered them and the year, and marks the registrations it has verified. We do not check them
on the NMC register ourselves (or, for dentists, on the Dental Council's), and the note shown
says so (Step2-Plan question 3, answered "no" for now).

Levels:
- practo_verified: a number, a council and a year, and Practo's mark;
- listed: a number and a council, but no mark (or no year);
- None: no number or no council. Such a doctor is never offered (tools/doctor_search.usable).

The registration number itself is never put in a note: the person needs to know a registration
exists and who issued it, not the number.
"""

from __future__ import annotations

from typing import Any

PRACTO_VERIFIED = "practo_verified"
LISTED = "listed"
NMC_REGISTER = "NMC register"
DENTAL_REGISTER = "Dental Council's register"
VERIFIED_NOTE = "Registration {year}, {council}, verified by Practo. Not checked by us on the {register}."
LISTED_NOTE = ("Registration listed on Practo ({details}), not marked verified by Practo. "
               "Not checked by us on the {register}.")
NO_REGISTRATION_NOTE = "No registration listed on Practo."


def _registration(record: Any) -> dict[str, Any]:
    registration = record.get("registration") if isinstance(record, dict) else None
    return registration if isinstance(registration, dict) else {}


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _year(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def registration_level(record: Any) -> str | None:
    """practo_verified, listed, or None (no number or no council)."""
    registration = _registration(record)
    if not (_text(registration.get("number")) and _text(registration.get("council"))):
        return None
    if _year(registration.get("year")) is not None and registration.get("practo_verified") is True:
        return PRACTO_VERIFIED
    return LISTED


def registration_note(record: Any) -> str:
    """A fixed sentence about the doctor's registration, for the answer."""
    level = registration_level(record)
    if level is None:
        return NO_REGISTRATION_NOTE
    registration = _registration(record)
    council, year = _text(registration["council"]), _year(registration.get("year"))
    dental = record.get("specialty") == "dentist" or "dental" in council.casefold()
    register = DENTAL_REGISTER if dental else NMC_REGISTER
    if level == PRACTO_VERIFIED:
        return VERIFIED_NOTE.format(year=year, council=council, register=register)
    details = f"{council}, {year}" if year is not None else council
    return LISTED_NOTE.format(details=details, register=register)
