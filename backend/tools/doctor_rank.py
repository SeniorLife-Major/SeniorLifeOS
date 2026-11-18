"""Order the doctors found and pick the top 3 (doctor search step 2f, Decision 17).

A fixed formula: no model and no network, so the order can't be made up, the same doctors always
come out in the same order, and every score can be explained from its parts (score_parts), which
the explain step (2g) builds its reasons from.

Factors (Step2-Plan 2f; weights revised in Decision 18), each scored 0-1 and weighted (WEIGHTS):
- specialty: 1 if Practo lists the doctor's specialty first (a branch of it, such as "Spine
  Surgeon (Ortho)", counts as the specialty: SAME_SPECIALTY), SECONDARY_SPECIALTY if only after
  another one;
- distance: 1 - km / the search radius used (5, 10 or 20 km);
- reviews: the recommend % pulled towards the middle (median) % of the doctors found, as if
  PRIOR_STORIES more stories had that %, so a few stories count for less than many; then 50% -> 0,
  100% -> 1;
- experience: years in the specialty (overall years when Practo gives none), counted up to
  YEARS_FULL, and the highest degree (degree_level);
- accessibility and insurance: no data yet, the same for everyone; seen before: no history yet, 0;
- language: only when the person asked for one; not listed counts half, since Practo's lists are
  thin (Hindi is listed for 14 of 179 doctors);
- fee: FEE_BEST rupees or less -> 1, FEE_WORST or more -> 0, a straight line between.

A missing value is never better than a real one: it takes the middle score of the doctors found.

Top 3: only doctors whose registration Practo verified (step 2e), and never one rated below
LOW_RATING_PERCENT in at least one patient story (the user's rule, Decision 17); the reason a doctor
was held back is kept. With fewer such doctors the top is shorter. Equal scores: nearer first.
"""

from __future__ import annotations

import math
import re
from statistics import median
from typing import Any

from .doctor_verify import PRACTO_VERIFIED, registration_level
from .provider_import import PRACTO_SPECIALTIES

# Specialty 0.20 -> 0.10, distance and reviews 0.15 -> 0.20 (Decision 18): at 0.20, listing the
# specialty second outweighed ~6.7 km and changed the top 3 in 28 of 357 real searches.
WEIGHTS = {"specialty": 0.10, "distance": 0.20, "reviews": 0.20, "experience": 0.10, "accessibility": 0.10,
           "insurance": 0.10, "language": 0.08, "fee": 0.07, "seen_before": 0.05}
TOP = 3
LOW_RATING_PERCENT = 70  # recommended by fewer: never in the top 3
PRIOR_STORIES = 10
SECONDARY_SPECIALTY = 0.5
YEARS_FULL = 25
YEARS_SHARE = 0.6  # of the experience score; the rest is the highest degree
FEE_BEST, FEE_WORST = 500, 3000
LANGUAGE_NOT_LISTED = 0.5
UNKNOWN_SCORE = 0.5  # a factor no doctor found has data for: the same for everyone
NO_DATA = "no data"
NOT_VERIFIED, LOW_RATING = "registration_not_verified", "low_rating"  # why a doctor was held back

# Names Practo lists first that are a branch of the specialty, not another one (Decision 18).
# For ranking only: what the import keeps is decided by PRACTO_SPECIALTIES alone.
SAME_SPECIALTY = {"orthopedic": r"\(ortho\)", "dentist": r"\bimplantolog"}
_SPECIALTY_NAMES = {key: re.compile("|".join(filter(None, (pattern, SAME_SPECIALTY.get(key)))), re.IGNORECASE)
                    for key, (_, pattern) in PRACTO_SPECIALTIES.items()}
# Highest first. A DNB in a super-speciality subject is a super-speciality degree (32 cardiologists
# in the list hold "DNB - Cardiology" without a DM).
_SUPER_DNB = re.compile(r"\bDNB\b[^,]*?(?:cardiolog|nephrolog|endocrinolog|gastroenterolog|neurolog|neurosurg"
                        r"|medical oncolog|surgical oncolog|rheumatolog|immunolog|urolog|ha?ematolog|thoracic"
                        r"|plastic|neonatolog|critical care)", re.IGNORECASE)
DEGREES = (
    ("super_specialty", 1.0, re.compile(r"\b(?:DM|M\.?\s?Ch|DrNB)\b")),
    ("post_graduate", 0.7, re.compile(r"\b(?:MD|MS|DNB|MDS|MRCP|FRCP|MRCS|FRCS)\b")),
    ("diploma", 0.5, re.compile(r"\bDiploma\b|\b(?:DGO|DOMS|DLO|DCH|DVD|DPM|DDVL|DTCD|DMRD|D\.?\s?Ortho)\b")),
    ("basic", 0.4, re.compile(r"\b(?:MBBS|BDS)\b")),
)
_DEGREE_SCORES = {name: score for name, score, _ in DEGREES}


def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _clip(value: float) -> float:
    return min(1.0, max(0.0, value))


def _middle(scores: list[float | None]) -> list[float]:
    """Missing scores (None) take the median of the known ones; with none known, UNKNOWN_SCORE."""
    known = [score for score in scores if score is not None]
    middle = median(known) if known else UNKNOWN_SCORE
    return [middle if score is None else score for score in scores]


def degree_level(qualifications: Any) -> str | None:
    """super_specialty, post_graduate, diploma, basic, or None if no known degree is named."""
    if not isinstance(qualifications, str):
        return None
    if _SUPER_DNB.search(qualifications):
        return "super_specialty"
    return next((name for name, _, pattern in DEGREES if pattern.search(qualifications)), None)


def rating(doctor: dict[str, Any]) -> tuple[int, int] | None:
    """(recommend %, patient stories), or None when no patient story backs a %."""
    reviews = doctor.get("reviews") if isinstance(doctor.get("reviews"), dict) else {}
    percent, stories = reviews.get("recommend_percent"), reviews.get("patient_stories")
    if _count(percent) and 0 <= percent <= 100 and _count(stories) and stories > 0:
        return percent, stories
    return None


def low_rated(doctor: dict[str, Any]) -> bool:
    found = rating(doctor)
    return found is not None and found[0] < LOW_RATING_PERCENT


def _years(doctor: dict[str, Any]) -> int | None:
    for key in ("specialist_years", "experience_years"):  # Practo's 0 specialist years: not given
        years = doctor.get(key)
        if _count(years) and years > 0:
            return years
    return None


def _first_speciality(doctor: dict[str, Any]) -> str | None:
    listed = doctor.get("practo_specialities")
    return listed[0] if isinstance(listed, list) and listed and isinstance(listed[0], str) else None


def _parts(doctors: list[dict[str, Any]], radius_km: float, language: str | None) -> list[dict[str, dict]]:
    """Each doctor's score_parts: {factor: {"weight", "score", "value"}}; value NO_DATA when missing."""
    ratings = [rating(doctor) for doctor in doctors]
    known = [found[0] for found in ratings if found]
    prior = median(known) if known else None
    counted = [None if found is None else (found[1] * found[0] + PRIOR_STORIES * prior) / (found[1] + PRIOR_STORIES)
               for found in ratings]  # found = (percent, stories)
    firsts = [_first_speciality(doctor) for doctor in doctors]
    specialty = _middle([None if first is None or doctor.get("specialty") not in _SPECIALTY_NAMES
                         else 1.0 if _SPECIALTY_NAMES[doctor["specialty"]].search(first) else SECONDARY_SPECIALTY
                         for doctor, first in zip(doctors, firsts)])
    reviews = _middle([None if value is None else _clip((value - 50) / 50) for value in counted])
    years = [_years(doctor) for doctor in doctors]
    degrees = [degree_level(doctor.get("qualifications")) for doctor in doctors]
    experience = [YEARS_SHARE * year + (1 - YEARS_SHARE) * degree for year, degree in zip(
        _middle([None if year is None else min(year, YEARS_FULL) / YEARS_FULL for year in years]),
        _middle([_DEGREE_SCORES.get(degree) for degree in degrees]))]
    fees = [doctor.get("fee") if _count(doctor.get("fee")) and doctor["fee"] >= 0 else None for doctor in doctors]
    fee = _middle([None if value is None else _clip((FEE_WORST - value) / (FEE_WORST - FEE_BEST)) for value in fees])
    asked = language.strip() if isinstance(language, str) and language.strip() else None
    parts = []
    for index, doctor in enumerate(doctors):
        languages = [name.casefold() for name in doctor.get("languages") or [] if isinstance(name, str)]
        listed = asked is not None and asked.casefold() in languages
        found = ratings[index]
        values = {
            "specialty": (specialty[index], firsts[index] or NO_DATA),
            "distance": (_clip(1 - doctor["distance_km"] / radius_km), doctor["distance_km"]),
            "reviews": (reviews[index], NO_DATA if found is None else {
                "recommend_percent": found[0], "patient_stories": found[1], "counted_as": round(counted[index], 1)}),
            "experience": (experience[index], {"years": years[index], "degree": degrees[index]}
                           if years[index] is not None or degrees[index] is not None else NO_DATA),
            "accessibility": (UNKNOWN_SCORE, NO_DATA),
            "insurance": (UNKNOWN_SCORE, NO_DATA),
            "language": ((1.0 if listed else LANGUAGE_NOT_LISTED), {"asked": asked, "listed": listed})
            if asked else (UNKNOWN_SCORE, NO_DATA),
            "fee": (fee[index], NO_DATA if fees[index] is None else fees[index]),
            "seen_before": (0.0, NO_DATA),
        }
        parts.append({factor: {"weight": WEIGHTS[factor], "score": round(score, 3), "value": value}
                      for factor, (score, value) in values.items()})
    return parts


def rank_doctors(doctors: list[dict[str, Any]], radius_km: float,
                 language: str | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(every doctor, the top first and then the rest best first; the top, at most TOP). Each
    doctor gets score (0-100), score_parts and held_back (None, NOT_VERIFIED or LOW_RATING)."""
    if not doctors:
        return [], []
    radius_km = radius_km if isinstance(radius_km, (int, float)) and math.isfinite(radius_km) and radius_km > 0 \
        else max(doctor["distance_km"] for doctor in doctors) or 1
    scored = []
    for doctor, parts in zip(doctors, _parts(doctors, radius_km, language)):
        total = round(100 * sum(part["weight"] * part["score"] for part in parts.values()), 1)
        held_back = (NOT_VERIFIED if registration_level(doctor) != PRACTO_VERIFIED
                     else LOW_RATING if low_rated(doctor) else None)
        scored.append({**doctor, "score": total, "score_parts": parts, "held_back": held_back})
    scored.sort(key=lambda doctor: (-doctor["score"], doctor["distance_km"], doctor["doctor_id"]))
    top = [doctor for doctor in scored if doctor["held_back"] is None][:TOP]
    chosen = {doctor["doctor_id"] for doctor in top}
    return [*top, *(doctor for doctor in scored if doctor["doctor_id"] not in chosen)], top
