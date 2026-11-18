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

How old (Decision 12): a record fetched up to FRESH_DAYS ago is used as it is; up to KEEP_DAYS
ago it is still shown, marked stale (the answer says the details may have changed); older, it
is never shown and is deleted from the file. A record without a valid date is never shown.

Wrong places (Decision 14): a clinic more than CITY_SPREAD_KM from the middle of the other clinics
Practo puts in the same city has wrong coordinates (seen live: a "Chembur, Mumbai" clinic placed
in Bengaluru) and is never shown.

Registration (Decision 16): a doctor with no registration number or council on Practo is never
offered, so the widening looks for doctors who have one (tools/doctor_verify.py).
"""

from __future__ import annotations

import json
import math
import threading
from datetime import date
from pathlib import Path
from statistics import median
from typing import Any

from .doctor_verify import registration_level
from .geocode import distance_km, in_india
from .provider_import import (
    DEFAULT_LIST, KEEP_DAYS, ProviderImportError, expired, fetched_date, remove_expired, today_utc,
)
from .specialties import SPECIALTIES

RADII_KM = (5, 10, 20)
MIN_DOCTORS = 3
MAX_CANDIDATES = 20  # kept for ranking and "show more"
GENDERS = ("female", "male")
DOCTOR_LIST = DEFAULT_LIST
FRESH_DAYS = 30  # older records are shown marked stale (Decision 12)
CLOCK_SLACK_DAYS = 1  # Apify's date may be a day ahead of this machine's
CITY_SPREAD_KM = 50  # farther from the middle of its city's clinics: wrong coordinates
MIN_CITY_CLINICS = 3  # a city with fewer clinics can't be checked


class DoctorListError(RuntimeError):
    """The stored doctor list is missing or can't be read."""


_cache: dict[str, Any] = {}
_cache_lock = threading.Lock()


def usable(record: Any) -> bool:
    """A record the search can use: our specialty, a gender, a clinic with a location in India, and
    a registration listed on Practo."""
    if not isinstance(record, dict) or not isinstance(record.get("doctor_id"), str):
        return False
    if registration_level(record) is None:
        return False
    clinic = record.get("clinic")
    if record.get("specialty") not in SPECIALTIES or record.get("gender") not in GENDERS or not isinstance(clinic, dict):
        return False
    lat, lon = clinic.get("lat"), clinic.get("lon")
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (lat, lon)):
        return False
    fee = record.get("fee")
    return in_india(lat, lon) and (fee is None or (isinstance(fee, int) and not isinstance(fee, bool) and fee >= 0))


def _city(record: dict[str, Any]) -> str | None:
    city = record["clinic"].get("city")
    return city.strip().casefold() if isinstance(city, str) and city.strip() else None


def well_placed(doctors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The records whose clinic lies within CITY_SPREAD_KM of the middle (median) of the clinics
    Practo puts in the same city. A city with fewer than MIN_CITY_CLINICS clinics is not checked."""
    points: dict[str, set[tuple[float, float]]] = {}
    for record in doctors:
        if _city(record):
            points.setdefault(_city(record), set()).add((record["clinic"]["lat"], record["clinic"]["lon"]))
    middles = {city: {"lat": median(lat for lat, _ in found), "lon": median(lon for _, lon in found)}
               for city, found in points.items() if len(found) >= MIN_CITY_CLINICS}
    return [record for record in doctors
            if _city(record) not in middles or distance_km(record["clinic"], middles[_city(record)]) <= CITY_SPREAD_KM]


def _age_days(record: dict[str, Any], today: date) -> int | None:
    fetched = fetched_date(record)
    return (today - fetched).days if fetched else None


def load_doctors(path: Path | None = None, today: date | None = None) -> list[dict[str, Any]]:
    """The usable records of the stored list fetched at most KEEP_DAYS ago, each with stale
    (older than FRESH_DAYS). Older records are deleted from the file. Read again only when the
    file or the day changes."""
    path = Path(path or DOCTOR_LIST)
    today = today or today_utc()
    try:
        stat = path.stat()
    except OSError:
        raise DoctorListError("The doctor list is missing; build it with tools/provider_import.py") from None
    stamp = (str(path), stat.st_mtime_ns, stat.st_size, today)
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
    doctors = []
    for record in records:
        age = _age_days(record, today) if usable(record) else None
        if age is not None and -CLOCK_SLACK_DAYS <= age <= KEEP_DAYS:
            doctors.append({**record, "stale": age > FRESH_DAYS})
    doctors = well_placed(doctors)
    keep_in_memory = True
    if any(expired(record, today) for record in records):
        keep_in_memory = False  # the file is about to change: read it again next time
        try:
            remove_expired(path, today)
        except (OSError, ProviderImportError):
            pass  # not deleted this time; never shown, and deleted on a later read or import
    if not doctors:
        raise DoctorListError("The doctor list has no current doctors; refresh it with tools/provider_import.py")
    if keep_in_memory:
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
