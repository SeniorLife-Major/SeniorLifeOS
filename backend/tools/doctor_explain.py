"""The answer in plain words (doctor search step 2g, Decision 19).

Fixed sentences filled from the data: no model and no network. Nothing in an answer can be made
up, it costs nothing, it works without the person's medical consent, and the same doctors always
give the same words. The facts are read from the ranking's score_parts (step 2f), so the reasons
match the numbers that ordered the doctors. The score itself is never shown, nor any
registration number.

For each doctor shown (the top, or, when nobody qualified for it, the best TOP found with why
they are not a first choice):
- reasons: the reviews, the experience, the fee, and the language if the person asked for one;
  a missing one is said plainly ("Fee not listed: ask the clinic");
- labels: what this doctor clearly has best among those shown (nearest, most recommended,
  lowest fee); "most recommended" needs FEW_REVIEWS stories or more;
- flags: warnings (why not a first choice, few reviews, a fee FEE_GAP or more above the cheapest
  shown, details over 30 days old).
The answer (plain text, for a chat or voice reply) adds how many other doctors were held back and
why (counted, never named), the notes passed in, how soon, and the sources.

Practo's text was cleaned at import and is cleaned again here (one line, length limits); the app
must still escape it when showing it as HTML. English for now: a model rewrite into the person's
language, checked against these facts, may come after the live run (Decision 19).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from .doctor_rank import LOW_RATING, LOW_RATING_PERCENT, NO_DATA, NOT_VERIFIED, TOP
from .provider_import import fetched_date

FEW_REVIEWS = 10  # fewer patient stories: flagged
FEE_GAP = 500  # rupees above the cheapest doctor shown: flagged
NO_REVIEWS = "No patient reviews on Practo yet"
NO_EXPERIENCE = "Experience not listed"
NO_FEE = "Fee not listed: ask the clinic"
NEAREST, MOST_RECOMMENDED, LOWEST_FEE = "Nearest", "Most recommended", "Lowest fee"
NOT_VERIFIED_FLAG = "Not a first choice: Practo has not verified the registration"
HELD_BACK_REASONS = {LOW_RATING: f"fewer than {LOW_RATING_PERCENT}% of their patients recommend them",
                     NOT_VERIFIED: "Practo has not verified their registration"}
URGENCY_NOTES = {"urgent": "Call the clinic today. If it gets worse suddenly, call 108.",
                 "soon": "Try to see a doctor in the next few days."}
SOURCES = "Doctor details from Practo, checked on {day}. Call the clinic before going."
PLACE_CREDIT = "Place search: {attribution}"

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _plain(value: Any, limit: int) -> str | None:
    """One line of plain text, at most limit characters, or None."""
    if not isinstance(value, str):
        return None
    return " ".join(_CONTROL.sub(" ", value).split())[:limit] or None


def _day(day: date) -> str:
    return f"{day.day} {day:%b %Y}"


def _stories(count: int) -> str:
    return f"{count} patient {'story' if count == 1 else 'stories'}"


def _count_of(count: int) -> str:
    return f"{count} other {'doctor' if count == 1 else 'doctors'} found {'was' if count == 1 else 'were'} not picked"


def _value(doctor: dict[str, Any], factor: str) -> Any:
    """What the ranking used for this factor, or None when it had no data."""
    part = (doctor.get("score_parts") or {}).get(factor) or {}
    value = part.get("value")
    return None if value == NO_DATA else value


def _fee(doctor: dict[str, Any]) -> int | None:
    fee = _value(doctor, "fee")
    return fee if isinstance(fee, int) and not isinstance(fee, bool) else None


def _experience(doctor: dict[str, Any]) -> str:
    """The years the ranking used, saying which: years as a specialist when Practo lists them (with
    the years in all when more), else the years in all."""
    years = (_value(doctor, "experience") or {}).get("years")
    if not years:
        return NO_EXPERIENCE
    if years == doctor.get("specialist_years"):
        total = doctor.get("experience_years")
        more = isinstance(total, int) and not isinstance(total, bool) and total > years
        return f"{years} {'year' if years == 1 else 'years'} as a specialist" + (f", {total} in all" if more else "")
    return "1 year's experience" if years == 1 else f"{years} years' experience"


def _reasons(doctor: dict[str, Any]) -> list[str]:
    reviews = _value(doctor, "reviews")
    reasons = [f"{reviews['recommend_percent']}% recommend ({_stories(reviews['patient_stories'])})"
               if reviews else NO_REVIEWS]
    text = _experience(doctor)
    degrees = _plain(doctor.get("qualifications"), 120)
    reasons.append(f"{text} ({degrees})" if degrees else text)
    fee = _fee(doctor)
    reasons.append(f"Fee ₹{fee:,}" if fee is not None else NO_FEE)
    language = _value(doctor, "language")
    asked = _plain(language.get("asked"), 40) if isinstance(language, dict) else None
    if asked:
        reasons.append(f"Speaks {asked}" if language.get("listed") else f"{asked} not listed on Practo")
    return reasons


def _clear_best(values: list[float | None], lowest: bool) -> int | None:
    """The index of the one doctor with the best value, or None when fewer than two values are
    known or the best is shared."""
    known = [value for value in values if value is not None]
    if len(known) < 2:
        return None
    best = min(known) if lowest else max(known)
    winners = [index for index, value in enumerate(values) if value == best]
    return winners[0] if len(winners) == 1 else None


def _labels(shown: list[dict[str, Any]]) -> list[list[str]]:
    labels: list[list[str]] = [[] for _ in shown]
    reviews = [_value(doctor, "reviews") for doctor in shown]
    for label, values, lowest in (
            (NEAREST, [doctor["distance_km"] for doctor in shown], True),
            (MOST_RECOMMENDED, [found["counted_as"] if found and found["patient_stories"] >= FEW_REVIEWS else None
                                for found in reviews], False),  # not with a few-reviews flag (Decision 20)
            (LOWEST_FEE, [_fee(doctor) for doctor in shown], True)):
        best = _clear_best(values, lowest)
        if best is not None:
            labels[best].append(label)
    return labels


def _flags(doctor: dict[str, Any], shown: list[dict[str, Any]]) -> list[str]:
    flags = []
    reviews = _value(doctor, "reviews")
    if doctor.get("held_back") == LOW_RATING and reviews:
        flags.append(f"Not a first choice: only {reviews['recommend_percent']}% recommend "
                     f"({_stories(reviews['patient_stories'])})")
    elif doctor.get("held_back") == NOT_VERIFIED:
        flags.append(NOT_VERIFIED_FLAG)
    if reviews and reviews["patient_stories"] < FEW_REVIEWS:
        flags.append(f"Only {_stories(reviews['patient_stories'])}")
    fees = [_fee(other) for other in shown]
    fee = _fee(doctor)
    if fee is not None and any(other is not None for other in fees):
        cheapest = min(other for other in fees if other is not None)
        if fee - cheapest >= FEE_GAP:
            flags.append(f"Fee ₹{fee - cheapest:,} more than option {fees.index(cheapest) + 1}")
    if doctor.get("stale") and fetched_date(doctor):
        flags.append(f"Details from {_day(fetched_date(doctor))}, may have changed")
    return flags


def _block(number: int, doctor: dict[str, Any]) -> list[str]:
    clinic = doctor.get("clinic") or {}
    speciality = _plain(_value(doctor, "specialty"), 60)
    where = ", ".join(filter(None, (_plain(clinic.get("name"), 120), _plain(clinic.get("locality"), 80))))
    heading = f"{number}. {_plain(doctor.get('name'), 80) or 'Name not listed'}"
    heading += f", {speciality}" if speciality else ""
    heading += f": {where}, {doctor['distance_km']} km" if where else f": {doctor['distance_km']} km"
    lines = [heading, "   " + " · ".join(doctor["reasons"])]
    if doctor["labels"]:
        lines.append("   " + " · ".join(doctor["labels"]))
    if doctor["flags"]:
        lines.append("   " + " · ".join(f"⚠️ {flag}" for flag in doctor["flags"]))
    note = _plain(doctor.get("registration_note"), 300)
    if note:
        lines.append(f"   {note}")
    hours, address = _plain(clinic.get("hours"), 120), _plain(clinic.get("address"), 250)
    url = _plain(doctor.get("profile_url"), 300)
    visit = [f"Open {hours}" if hours else None, address, f"Book or read more: {url}" if url else None]
    if any(visit):
        lines.append("   " + " · ".join(filter(None, visit)))
    return lines


def explain_doctors(doctors: list[dict[str, Any]], top: list[dict[str, Any]], label: str,
                    place: dict[str, Any], notes: list[str] = (), urgency: str | None = None) -> dict[str, Any]:
    """{"doctors", "top_doctors", "answer"}: the doctors shown gain reasons, labels and flags (in
    both lists); answer is the whole reply as plain text."""
    shown = list(top) or list(doctors[:TOP])
    labels = _labels(shown)
    explained = [{**doctor, "reasons": _reasons(doctor), "labels": labels[index], "flags": _flags(doctor, shown)}
                 for index, doctor in enumerate(shown)]
    place_label = _plain(place.get("label"), 120) or "the place you asked for"
    count = len(explained)
    lines = [f"{label} near {place_label}: "
             + (f"{count} best {'match' if count == 1 else 'matches'}" if top else f"{count} found, none a first choice"),
             ""]
    for number, doctor in enumerate(explained, 1):
        lines.extend([*_block(number, doctor), ""])
    shown_ids = {doctor["doctor_id"] for doctor in explained}
    if top:
        for reason, why in HELD_BACK_REASONS.items():
            held = sum(doctor.get("held_back") == reason and doctor["doctor_id"] not in shown_ids for doctor in doctors)
            if held:
                lines.append(f"{_count_of(held)}: {why}.")
    lines.extend(filter(None, (_plain(note, 300) for note in notes)))
    if urgency in URGENCY_NOTES:
        lines.append(URGENCY_NOTES[urgency])
    checked = sorted(filter(None, (fetched_date(doctor) for doctor in explained)))
    if checked:
        lines.append(SOURCES.format(day=_day(checked[0])))  # the oldest date shown
    attribution = _plain(place.get("attribution"), 120)
    if attribution:
        lines.append(PLACE_CREDIT.format(attribution=attribution))
    by_id = {doctor["doctor_id"]: doctor for doctor in explained}
    return {"doctors": [by_id.get(doctor["doctor_id"], doctor) for doctor in doctors],
            "top_doctors": [by_id[doctor["doctor_id"]] for doctor in top],
            "answer": "\n".join(lines).strip()}
