"""Build the stored doctor list from Practo, through the Apify actor logiover/practo-scraper
(doctor search step 2c). Run by hand, not for each request (doctor-search Decisions 7 and 9):

    python -m backend.tools.provider_import --city Mumbai --locality "Andheri West" --dry-run
    python -m backend.tools.provider_import --city Mumbai --locality "Andheri West"

One search per specialty and gender: the actor has no gender column but can filter by it, so
searching female and male doctors separately labels each one at no extra cost.

Spending: the actor charges per result (PRICE_PER_RESULT). Each search is capped on Apify's side
(maxItems, maxTotalChargeUsd) and the whole import stops before its worst case could pass
--max-spend.

What is kept: only the fields the search, check and ranking use. No phone numbers (lookup is
off), no photos, no free-text "about". Doctors' names and registration details are personal data
(DPDP Act 2023), so the list lives in var/ (git-ignored). Every value from the actor is plain
data: checked, capped, never instructions.

How long: a record fetched more than KEEP_DAYS ago is deleted from the list (doctor-search
Decision 12): a doctor may have moved or stopped practising, and old personal data is not kept.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from ..utils.env import REPO_DIR, find_env_file, load_env_file
from .geocode import in_india
from .specialties import SPECIALTIES

ACTOR = "logiover~practo-scraper"
RUN_URL = f"https://api.apify.com/v2/acts/{ACTOR}/run-sync-get-dataset-items"
KEY_NAME = "APIFY_API"
SOURCE = "Practo, via Apify (logiover/practo-scraper)"
PRICE_PER_RESULT = 0.0025  # USD per result, free plan (the actor's pricing, read 2026-11-17)
DEFAULT_PER_SEARCH = 3
DEFAULT_MAX_SPEND = 0.30
RUN_TIMEOUT_SECONDS = 300
GENDERS = ("female", "male")
DEFAULT_LIST = REPO_DIR / "var" / "providers" / "doctors.json"
PROFILE_PREFIX = "https://www.practo.com/"
COORD_DECIMALS = 5  # a clinic's public address: kept precise for distances
MAX_CLINICS = 5
MAX_LIST_ITEMS = 10
KEEP_DAYS = 90  # older records are deleted (Decision 12)
SAVE_TRIES = 3  # the list changed while it was being saved: read it again and merge
CONTROL = re.compile(r"[\x00-\x1f\x7f]")

# Our specialty -> (the name Practo searches by, what a returned doctor's specialities must match).
# Practo also shows related doctors; one whose specialities don't match is not kept.
PRACTO_SPECIALTIES: dict[str, tuple[str, str]] = {
    "general_physician": ("General Physician", r"\b(?:general physician|internal medicine|family physician|"
                                               r"general practitioner)"),
    "geriatrician": ("Geriatrician", r"\bgeriatric"),
    "cardiologist": ("Cardiologist", r"\bcardiolog"),
    "neurologist": ("Neurologist", r"\bneurolog"),
    "orthopedic": ("Orthopedist", r"\borthopa?edi"),
    "rheumatologist": ("Rheumatologist", r"\brheumatolog"),
    "pulmonologist": ("Pulmonologist", r"\b(?:pulmonolog|chest physician|respiratory)"),
    "gastroenterologist": ("Gastroenterologist", r"\bgastroenterolog"),
    "nephrologist": ("Nephrologist", r"\bnephrolog"),
    "urologist": ("Urologist", r"\burolog"),
    "endocrinologist": ("Endocrinologist", r"\b(?:endocrinolog|diabetolog)"),
    "dermatologist": ("Dermatologist", r"\bdermatolog"),
    "ophthalmologist": ("Ophthalmologist", r"\b(?:ophthalmolog|eye)"),
    "ent": ("ENT", r"\b(?:ent\b|ear-nose-throat|otorhinolaryngolog|otolaryngolog)"),
    "psychiatrist": ("Psychiatrist", r"\bpsychiatr"),
    "gynecologist": ("Gynecologist", r"\b(?:gynecolog|gynaecolog|obstetric)"),
    "oncologist": ("Oncologist", r"\boncolog"),
    "general_surgeon": ("General Surgeon", r"\bgeneral surgeon"),
    "dentist": ("Dentist", r"\b(?:dentist|dental|orthodontist|endodontist|prosthodontist|periodontist|oral)"),
}
_MATCHES = {key: re.compile(pattern, re.IGNORECASE) for key, (_, pattern) in PRACTO_SPECIALTIES.items()}


class ProviderImportError(RuntimeError):
    """The import could not go on. Never holds the key."""


def apify_key() -> str | None:
    load_env_file(find_env_file())
    return os.environ.get(KEY_NAME, "").strip() or None


def clean_text(value: Any, limit: int) -> str | None:
    """Plain one-line text, capped; None if empty or not text."""
    if not isinstance(value, str):
        return None
    text = " ".join(CONTROL.sub(" ", value).split())[:limit].strip()
    return text or None


def clean_int(value: Any, low: int, high: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    number = int(value)
    return number if low <= number <= high else None


def clean_list(values: Any, limit: int) -> list[str]:
    if not isinstance(values, list):
        return []
    cleaned = [clean_text(value, limit) for value in values[:MAX_LIST_ITEMS]]
    return [value for value in cleaned if value]


def clinic_point(lat: Any, lon: Any) -> dict[str, float] | None:
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (lat, lon)):
        return None
    if not in_india(lat, lon):
        return None
    return {"lat": round(lat, COORD_DECIMALS), "lon": round(lon, COORD_DECIMALS)}


def today_utc() -> date:
    return datetime.now(timezone.utc).date()


def fetched_date(record: Any) -> date | None:
    """The day a stored record was fetched, or None if it has no valid date."""
    value = record.get("fetched_on") if isinstance(record, dict) else None
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def expired(record: Any, today: date) -> bool:
    """Fetched more than KEEP_DAYS ago. A record with no valid date is not counted as expired (its
    age is unknown, so it is not deleted); the search never shows it either."""
    fetched = fetched_date(record)
    return fetched is not None and (today - fetched).days > KEEP_DAYS


def search_key(city: str, locality: str | None, specialty: str) -> str:
    """How a search is remembered in the list's "searches": "mumbai|andheri west|cardiologist"."""
    return f"{' '.join(city.split())}|{' '.join((locality or '').split())}|{specialty}".casefold()


def _fetched_on(item: dict[str, Any]) -> str:
    try:
        return datetime.fromisoformat(str(item.get("scrapedAt")).replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return datetime.now(timezone.utc).date().isoformat()


def _clinics(item: dict[str, Any]) -> list[dict[str, Any]]:
    """The doctor's clinics; the listing's own clinic fields if the list is missing."""
    clinics = [c for c in item.get("clinics") or [] if isinstance(c, dict)][:MAX_CLINICS]
    if clinics:
        return clinics
    return [{"clinicId": item.get("clinicId"), "name": item.get("clinicName"), "address": item.get("clinicAddress"),
             "locality": item.get("clinicLocality"), "city": item.get("clinicCity"), "latitude": item.get("latitude"),
             "longitude": item.get("longitude"), "consultationFee": item.get("consultationFee"),
             "doctorTimings": item.get("doctorTimings")}]


def doctor_records(item: Any, specialty: str, gender: str) -> tuple[list[dict[str, Any]], str]:
    """One stored record per clinic of this doctor, or ([], why it was not kept)."""
    if not isinstance(item, dict) or item.get("recordType") != "doctor":
        return [], "not a doctor"
    doctor_id = clean_int(item.get("doctorId"), 1, 10 ** 12)
    name = clean_text(item.get("name"), 80)
    if doctor_id is None or not name:
        return [], "no Practo ID or name"
    practo_specialities = clean_list(item.get("specialities"), 60)
    headline = clean_text(item.get("specialization"), 60)
    if not any(_MATCHES[specialty].search(s) for s in [*practo_specialities, *([headline] if headline else [])]):
        return [], "another specialty"
    stories = clean_int(item.get("patientStories"), 0, 10 ** 7) or 0
    url = clean_text(item.get("url"), 300)
    this_year = datetime.now(timezone.utc).year
    base = {
        "doctor_id": f"practo:{doctor_id}",
        "name": name,
        "gender": gender,  # from the search's gender filter
        "specialty": specialty,
        "practo_specialities": practo_specialities or ([headline] if headline else []),
        "qualifications": clean_text(item.get("qualifications"), 200),
        "experience_years": clean_int(item.get("experienceYears"), 0, 80),
        "specialist_years": clean_int(item.get("specialistExperienceYears"), 0, 80),
        "languages": clean_list(item.get("languages"), 40),
        "registration": {
            "number": clean_text(item.get("registrationNumber"), 40),
            "council": clean_text(item.get("registrationCouncil"), 120),
            "year": clean_int(item.get("registrationYear"), 1940, this_year),
            "practo_verified": item.get("registrationVerified") is True,
        },
        "nmc_checked_on": None,  # set by hand after a check on the NMC register
        # No stories: no review data (seen live: "0%" with 0 stories), not a 0% score.
        "reviews": {"recommend_percent": clean_int(item.get("recommendationPercent"), 0, 100) if stories else None,
                    "patient_stories": stories},
        "profile_url": url if url and url.startswith(PROFILE_PREFIX) else None,
        "source": SOURCE,
        "fetched_on": _fetched_on(item),
    }
    records = []
    for clinic in _clinics(item):
        point = clinic_point(clinic.get("latitude"), clinic.get("longitude"))
        clinic_id = clean_int(clinic.get("clinicId"), 1, 10 ** 12)
        if point is None or clinic_id is None:
            continue
        records.append({
            "id": f"practo:{doctor_id}:{clinic_id}:{specialty}",
            **base,
            "fee": clean_int(clinic.get("consultationFee"), 0, 100_000),
            "clinic": {"name": clean_text(clinic.get("name"), 120), "address": clean_text(clinic.get("address"), 250),
                       "locality": clean_text(clinic.get("locality"), 80), "city": clean_text(clinic.get("city"), 60),
                       **point, "hours": clean_text(clinic.get("doctorTimings"), 300)},
        })
    return (records, "") if records else ([], "no clinic with a location in India")


def run_search(client: httpx.Client, key: str, city: str, locality: str | None, specialty: str, gender: str,
               per_search: int, run_timeout: int = RUN_TIMEOUT_SECONDS) -> list[Any]:
    """One actor run: up to per_search doctors. Capped on Apify's side by count, cost and time."""
    body = {"searchType": "doctors", "cities": [city], "localities": [locality] if locality else [],
            "specialities": [PRACTO_SPECIALTIES[specialty][0]], "gender": gender, "maxItems": per_search,
            "includePhone": False, "includeDetails": True}
    params = {"maxItems": per_search, "maxTotalChargeUsd": round(per_search * PRICE_PER_RESULT * 1.2, 4),
              "timeout": run_timeout}
    try:
        response = client.post(RUN_URL, params=params, json=body, headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError as exc:
        raise ProviderImportError(f"Apify could not be reached ({type(exc).__name__})") from None
    if response.status_code not in (200, 201):
        raise ProviderImportError(f"Apify run failed (HTTP {response.status_code})")  # body not shown
    try:
        items = response.json()
    except ValueError:
        raise ProviderImportError("Apify sent an answer that could not be read") from None
    if not isinstance(items, list):
        raise ProviderImportError("Apify sent an answer that could not be read")
    return items


def load_list(path: Path) -> dict[str, Any]:
    """The stored list, or an empty one. A file that can't be read stops the import: it is never
    overwritten by accident."""
    if not path.exists():
        return {"source": SOURCE, "doctors": []}
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(stored, dict) or not isinstance(stored.get("doctors"), list):
            raise ValueError
    except ValueError:
        raise ProviderImportError(f"{path} is not a doctor list; move it away first") from None
    return stored


def _write_list(path: Path, stored: dict[str, Any], unchanged: os.stat_result | None = None) -> bool:
    """Write the list whole or not at all. With unchanged: only if the file is still as it was
    then (an import may have saved meanwhile); False if it was not written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump(stored, file, ensure_ascii=False, indent=1)
        if unchanged is not None:
            now = path.stat()
            if (now.st_mtime_ns, now.st_size) != (unchanged.st_mtime_ns, unchanged.st_size):
                return False
        os.replace(temp, path)
        return True
    finally:
        if os.path.exists(temp):
            os.remove(temp)


def save_list(path: Path, records: list[dict[str, Any]], today: date | None = None,
              searches: dict[str, dict[str, Any]] | None = None) -> int:
    """Add these records (and these searches, see search_key) and keep the rest, except stored
    records and searches from more than KEEP_DAYS ago (deleted). A doctor fetched again replaces
    all their stored records for that specialty: the new listing names every clinic they work at
    now, so a clinic missing from it is one they left (Decision 13). The file is read again just
    before writing, so a save made meanwhile (a fetch for a request, a clean-up) is kept, not
    overwritten. Written whole or not at all."""
    today = today or today_utc()
    fetched_again = {(record["doctor_id"], record["specialty"]) for record in records}
    for _ in range(SAVE_TRIES):
        before = path.stat() if path.exists() else None
        stored = load_list(path)
        by_id = {record["id"]: record for record in stored["doctors"]
                 if isinstance(record, dict) and "id" in record and not expired(record, today)
                 and (record.get("doctor_id"), record.get("specialty")) not in fetched_again}
        by_id.update({record["id"]: record for record in records})
        known = stored.get("searches") if isinstance(stored.get("searches"), dict) else {}
        index = {key: entry for key, entry in known.items() if isinstance(entry, dict) and not expired(entry, today)}
        index.update(searches or {})
        new = {**stored, "source": SOURCE, "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "searches": dict(sorted(index.items())),
               "doctors": sorted(by_id.values(), key=lambda record: record["id"])}
        if _write_list(path, new, unchanged=before):
            return len(by_id)
    raise ProviderImportError("The doctor list kept changing while it was being saved; run the import again")


def remove_expired(path: Path, today: date | None = None) -> int:
    """Delete the records fetched more than KEEP_DAYS ago from the stored list; how many were
    deleted. Not written if the file changed while it was read: 0, and a later read tries again."""
    today = today or today_utc()
    before = path.stat()
    stored = load_list(path)
    kept = [record for record in stored["doctors"] if not expired(record, today)]
    removed = len(stored["doctors"]) - len(kept)
    if removed and not _write_list(path, {**stored, "doctors": kept}, unchanged=before):
        return 0
    return removed


def import_doctors(city: str, locality: str | None = None, specialties: list[str] | None = None,
                   per_search: int = DEFAULT_PER_SEARCH, max_spend: float = DEFAULT_MAX_SPEND,
                   out: Path = DEFAULT_LIST, client: httpx.Client | None = None,
                   dry_run: bool = False, run_timeout: int = RUN_TIMEOUT_SECONDS,
                   workers: int = 1) -> dict[str, Any]:
    """Search each specialty for female and male doctors, clean the rows, and add them to the
    stored list. A specialty whose searches all worked is remembered in the list's "searches"
    (city, locality, date), so it is not fetched again while fresh (tools/doctor_refresh.py).
    workers: how many Apify searches run at the same time (a person waiting: one per gender).
    Returns a report: each search, the specialties fully searched, what was kept and dropped, the
    cost."""
    specialties = specialties or list(PRACTO_SPECIALTIES)
    unknown = [s for s in specialties if s not in PRACTO_SPECIALTIES]
    if unknown:
        raise ProviderImportError(f"Unknown specialties: {', '.join(unknown)}")
    if not clean_text(city, 60) or per_search < 1 or max_spend <= 0:
        raise ProviderImportError("A city, at least 1 doctor per search and a spending limit above 0 are needed")
    searches = [(specialty, gender) for specialty in specialties for gender in GENDERS]
    worst_each = per_search * PRICE_PER_RESULT
    report: dict[str, Any] = {"city": city, "locality": locality, "searches": [], "searched": [], "kept": 0,
                              "dropped": {}, "results": 0, "cost_usd": 0.0, "skipped": [], "out": str(out),
                              "max_cost_usd": round(min(len(searches) * worst_each, max_spend), 4)}
    if dry_run:
        report["skipped"] = [f"{s}/{g}" for s, g in searches]
        return report
    key = apify_key()
    if not key:
        raise ProviderImportError(f"The import needs {KEY_NAME}")
    stored = load_list(out)  # before spending: a broken list stops the import here
    own_client = client is None
    client = client or httpx.Client(timeout=RUN_TIMEOUT_SECONDS + 30)
    records: list[dict[str, Any]] = []
    planned, worst_spent = [], 0.0
    for specialty, gender in searches:  # decided before any search runs, in order
        if worst_spent + worst_each > max_spend + 1e-9:
            report["skipped"].append(f"{specialty}/{gender}")
            continue
        worst_spent += worst_each
        planned.append((specialty, gender))

    def search(planned_search: tuple[str, str]) -> tuple[list[Any] | None, str | None]:
        try:
            return run_search(client, key, city, locality, *planned_search, per_search, run_timeout), None
        except ProviderImportError as exc:  # e.g. Practo has no such listing here: the others go on
            return None, str(exc)

    try:
        if workers > 1:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                outcomes = list(pool.map(search, planned))
        else:
            outcomes = [search(planned_search) for planned_search in planned]
    finally:
        if own_client:
            client.close()
    for (specialty, gender), (items, error) in zip(planned, outcomes):
        row = {"specialty": specialty, "gender": gender, "results": 0, "kept": 0, "error": error}
        report["searches"].append(row)
        if items is None:
            continue
        row["results"] = len(items)
        report["results"] += len(items)
        for item in items:
            kept, why = doctor_records(item, specialty, gender)
            records.extend(kept)
            row["kept"] += bool(kept)
            if why:
                report["dropped"][why] = report["dropped"].get(why, 0) + 1
    report["cost_usd"] = round(report["results"] * PRICE_PER_RESULT, 4)  # charged per result
    report["kept"] = len({record["doctor_id"] + record["specialty"] for record in records})
    report["clinic_records"] = len(records)
    worked = {(row["specialty"], row["gender"]) for row in report["searches"] if not row["error"]}
    report["searched"] = [s for s in specialties if all((s, gender) in worked for gender in GENDERS)]
    fetched_on = today_utc().isoformat()
    searches = {search_key(city, locality, specialty): {
        "city": city, "locality": locality, "specialty": specialty, "fetched_on": fetched_on,
        "per_search": per_search,
        "results": sum(row["results"] for row in report["searches"] if row["specialty"] == specialty),
    } for specialty in report["searched"]}
    if records or searches:  # an empty search is remembered too: it is not paid for again
        report["list_size"] = save_list(out, records, searches=searches)
    else:
        report["list_size"] = len(stored["doctors"])
    return report


def main(argv: list[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser(description="Add Practo doctors for one area to the stored doctor list.")
    parser.add_argument("--city", required=True, help='Practo city, e.g. "Mumbai"')
    parser.add_argument("--locality", help='Practo locality, e.g. "Andheri West"')
    parser.add_argument("--specialties", help="comma-separated, e.g. cardiologist,dentist (default: all 19)")
    parser.add_argument("--per-search", type=int, default=DEFAULT_PER_SEARCH, help="doctors per specialty and gender")
    parser.add_argument("--max-spend", type=float, default=DEFAULT_MAX_SPEND, help="US dollars, for the whole import")
    parser.add_argument("--out", type=Path, default=DEFAULT_LIST)
    parser.add_argument("--dry-run", action="store_true", help="show the searches and the most it can cost")
    args = parser.parse_args(argv)
    specialties = [s.strip() for s in args.specialties.split(",")] if args.specialties else None
    report = import_doctors(args.city, args.locality, specialties, args.per_search, args.max_spend, args.out,
                            dry_run=args.dry_run)
    print(f"{args.city} / {args.locality or 'whole city'}: at most ${report['max_cost_usd']}")
    for row in report["searches"]:
        print(f"  {row['specialty']:20s} {row['gender']:6s} results {row['results']}  kept {row['kept']}"
              + (f"  ERROR {row['error']}" if row["error"] else ""))
    if report["skipped"]:
        print(f"  not searched ({'dry run' if args.dry_run else 'spending limit'}): {', '.join(report['skipped'])}")
    if not args.dry_run:
        print(f"kept {report['kept']} doctors ({report['clinic_records']} clinic records), dropped {report['dropped']}, "
              f"cost ${report['cost_usd']}; list now {report['list_size']} records in {report['out']}")
    return report


if __name__ == "__main__":
    main()
