"""Doctors of one specialty near a place, from the stored list (doctor search step 2d).

The list (var/providers/doctors.json) is built by hand with tools/provider_import.py; nothing here
uses the network, so a search takes a moment and nothing about the person leaves the machine.

Search rules (doctor-search Step2-Plan 2d, accepted in Decision 9):
- only the asked specialty; each doctor once, at their nearest clinic;
- 5 km first, then 10, then 20, while fewer than MIN_DOCTORS are found;
- what the person asked for (doctor's gender, highest fee) is kept while widening: they asked for
  it, distance is only a default. A doctor whose fee is unknown is left out when a fee limit was
  asked for (it can't be shown to be within it);
- if nobody is found within 20 km, the fee limit is dropped first, then the gender, and the
  answer says which (relaxed). A lady doctor is often asked for comfort, so it is kept longest.
"""

from __future__ import annotations

import json
import math
import threading
from pathlib import Path
from typing import Any

from .geocode import distance_km, in_india
from .provider_import import DEFAULT_LIST
from .specialties import SPECIALTIES

RADII_KM = (5, 10, 20)
MIN_DOCTORS = 3
MAX_CANDIDATES = 20  # kept for ranking and "show more"
GENDERS = ("female", "male")
DOCTOR_LIST = DEFAULT_LIST


class DoctorListError(RuntimeError):
    """The stored doctor list is missing or can't be read."""


_cache: dict[str, Any] = {}
_cache_lock = threading.Lock()


def usable(record: Any) -> bool:
    """A record the search can use: our specialty, a gender, a clinic with a location in India."""
    if not isinstance(record, dict) or not isinstance(record.get("doctor_id"), str):
        return False
    clinic = record.get("clinic")
    if record.get("specialty") not in SPECIALTIES or record.get("gender") not in GENDERS or not isinstance(clinic, dict):
        return False
    lat, lon = clinic.get("lat"), clinic.get("lon")
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (lat, lon)):
        return False
    fee = record.get("fee")
    return in_india(lat, lon) and (fee is None or (isinstance(fee, int) and not isinstance(fee, bool) and fee >= 0))


def load_doctors(path: Path | None = None) -> list[dict[str, Any]]:
    """The usable records of the stored list; read again only when the file changes."""
    path = Path(path or DOCTOR_LIST)
    try:
        stat = path.stat()
    except OSError:
        raise DoctorListError("The doctor list is missing; build it with tools/provider_import.py") from None
    stamp = (str(path), stat.st_mtime_ns, stat.st_size)
    with _cache_lock:
        if _cache.get("stamp") == stamp:
            return _cache["doctors"]
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
        records = stored["doctors"]
        if not isinstance(records, list):
            raise TypeError
    except (OSError, ValueError, KeyError, TypeError):
        raise DoctorListError("The doctor list can't be read") from None
    doctors = [record for record in records if usable(record)]
    with _cache_lock:
        _cache.update(stamp=stamp, doctors=doctors)
    return doctors


def _wanted_gender(value: Any) -> str | None:
    return value if value in GENDERS else None


def _wanted_fee(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        return None
    return int(value)


def _within(doctors: list[dict[str, Any]], specialty: str, point: dict[str, float], radius_km: float,
            gender: str | None, max_fee: int | None) -> list[dict[str, Any]]:
    """Each matching doctor once, at their nearest clinic within the radius, nearest first."""
    nearest: dict[str, dict[str, Any]] = {}
    for record in doctors:
        if record["specialty"] != specialty or (gender and record["gender"] != gender):
            continue
        if max_fee is not None and (record.get("fee") is None or record["fee"] > max_fee):
            continue
        km = distance_km(record["clinic"], point)
        if km > radius_km:
            continue
        kept = nearest.get(record["doctor_id"])
        if kept is None or km < kept["distance_km"]:
            nearest[record["doctor_id"]] = {**record, "distance_km": km}
    found = sorted(nearest.values(), key=lambda doctor: (doctor["distance_km"], doctor["doctor_id"]))
    return [{**doctor, "distance_km": round(doctor["distance_km"], 1)} for doctor in found[:MAX_CANDIDATES]]


def find_nearby(doctors: list[dict[str, Any]], specialty: str, point: dict[str, float],
                gender: Any = None, max_fee: Any = None) -> dict[str, Any]:
    """{"doctors": [...nearest first, with distance_km], "radius_km": .., "relaxed": [...]}.
    relaxed names the person's wishes that had to be dropped: "max_fee", "doctor_gender"."""
    wishes = {"doctor_gender": _wanted_gender(gender), "max_fee": _wanted_fee(max_fee)}
    tries = [[]]  # the wishes dropped in each try: none, then the fee, then the gender too
    if wishes["max_fee"] is not None:
        tries.append(["max_fee"])
    if wishes["doctor_gender"] is not None:
        tries.append([*tries[-1], "doctor_gender"])
    for dropped in tries:
        kept = {name: (None if name in dropped else value) for name, value in wishes.items()}
        for radius in RADII_KM:
            found = _within(doctors, specialty, point, radius, kept["doctor_gender"], kept["max_fee"])
            if len(found) >= MIN_DOCTORS or (found and radius == RADII_KM[-1]):
                return {"doctors": found, "radius_km": radius, "relaxed": dropped}
    return {"doctors": [], "radius_km": RADII_KM[-1], "relaxed": tries[-1]}
