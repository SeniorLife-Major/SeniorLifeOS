"""Fetch a missing or out-of-date search from Practo while the person waits (doctor-search
Decision 13, part B of the cache plan).

The stored list (tools/provider_import.py) is a cache. Each Apify search it holds is remembered
by city, area and specialty ("searches" in the list). When a request needs a search that is not
there, or was made more than FRESH_DAYS ago, it is made now: one Apify search per gender, both at
the same time, FETCH_PER_SEARCH doctors each (at most WORST_COST_USD), saved like an import. If the fetch can't
be made or fails, the search goes on with what is stored (older details are marked by
tools/doctor_search.py).

Privacy: only with the person's medical consent (the graph checks it), because Apify learns the
specialty and the area name. For "near me" the area is the one Practo gives the nearest stored
clinic within NAME_FROM_LIST_KM (nothing is sent to name it); with none that near, Geoapify names
it from the phone's location rounded to about 1 km (tools/geocode.area_near, Decision 14). Apify
never gets coordinates.

Spending: at most DAILY_LIMIT_USD a day. The worst case is written down before each fetch
(var/providers/fetch_spend.json) and corrected after, so a crash can't hide spending. A search
that failed is not tried again for RETRY_AFTER_SECONDS. Fetches run one at a time, so two
requests never pay for the same search.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from .doctor_search import CLOCK_SLACK_DAYS, FRESH_DAYS
from .geocode import GeocodeError, area_near, distance_km
from .provider_import import (
    DEFAULT_LIST, GENDERS, PRICE_PER_RESULT, ProviderImportError, apify_key, fetched_date, import_doctors,
    load_list, search_key, today_utc,
)

FETCH_PER_SEARCH = 10
WORST_COST_USD = round(len(GENDERS) * FETCH_PER_SEARCH * PRICE_PER_RESULT, 4)  # $0.05
DAILY_LIMIT_USD = 1.00
FETCH_TIMEOUT_SECONDS = 60  # per Apify run; the person is waiting
RETRY_AFTER_SECONDS = 30 * 60
NAME_FROM_LIST_KM = 3
SPEND_FILE = DEFAULT_LIST.parent / "fetch_spend.json"

_lock = threading.Lock()
_failed_at: dict[str, float] = {}  # search key -> when it last failed (time.monotonic)


def area_of(place: dict[str, Any], doctors: list[dict[str, Any]]) -> tuple[str, str | None] | None:
    """(city, neighbourhood or None) to search Practo by, or None if not known. A looked-up place
    has its own names (tools/geocode.py). For the phone's location: the names Practo gives the
    nearest stored clinic within NAME_FROM_LIST_KM, else the nearest suburb, town or village
    Geoapify knows (the location rounded to about 1 km), else the city alone."""
    city, area = place.get("city"), place.get("area")
    if isinstance(city, str) and city.strip():
        return city.strip(), (area.strip() if isinstance(area, str) and area.strip() else None)
    nearest = min(doctors, key=lambda doctor: distance_km(doctor["clinic"], place), default=None)
    if nearest is not None and distance_km(nearest["clinic"], place) <= NAME_FROM_LIST_KM:
        city, area = nearest["clinic"].get("city"), nearest["clinic"].get("locality")
        if isinstance(city, str) and city.strip():
            return city.strip(), (area.strip() if isinstance(area, str) and area.strip() else None)
    try:
        found = area_near(place)
    except GeocodeError:  # no key, network, service error: not fetched this time
        return None
    return (found["city"], found["area"]) if found else None


def _fresh(stored: dict[str, Any], key: str, today: date) -> bool:
    searches = stored.get("searches") if isinstance(stored.get("searches"), dict) else {}
    fetched = fetched_date(searches.get(key))
    return fetched is not None and -CLOCK_SLACK_DAYS <= (today - fetched).days <= FRESH_DAYS


def spent_today(path: Path, today: date) -> float:
    """What today's fetches cost (or may have cost). A file that can't be read counts as the whole
    day's limit: no spending without a record of it."""
    if not path.exists():
        return 0.0
    try:
        spend = json.loads(path.read_text(encoding="utf-8"))
        usd = float(spend["usd"])
        if usd < 0:
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError):
        return DAILY_LIMIT_USD
    return usd if spend.get("day") == today.isoformat() else 0.0


def _write_spend(path: Path, today: date, usd: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump({"day": today.isoformat(), "usd": round(usd, 4)}, file)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.remove(temp)


def refresh_doctors(place: dict[str, Any], specialty: str, doctors: list[dict[str, Any]],
                    path: Path | None = None, spend_file: Path | None = None, today: date | None = None,
                    client: httpx.Client | None = None) -> dict[str, Any]:
    """Make sure the stored list holds a recent search for this specialty near the place.

    {"status": ..., "area": "Koregaon Park, Pune" or None, "results": n, "cost_usd": x}; status is
    fresh (nothing to do), fetched, no_area, no_key, over_budget, failed or failed_recently."""
    path, spend_file, today = Path(path or DEFAULT_LIST), Path(spend_file or SPEND_FILE), today or today_utc()
    found = area_of(place, doctors)
    if found is None:
        return {"status": "no_area", "area": None, "results": 0, "cost_usd": 0.0}
    city, locality = found
    key = search_key(city, locality, specialty)
    answer = {"area": ", ".join(part for part in (locality, city) if part), "results": 0, "cost_usd": 0.0}
    try:
        if _fresh(load_list(path), key, today):  # most requests end here, without waiting for a fetch
            return {**answer, "status": "fresh"}
        with _lock:
            if _fresh(load_list(path), key, today):  # fetched by a request that held the lock
                return {**answer, "status": "fresh"}
            if time.monotonic() - _failed_at.get(key, float("-inf")) < RETRY_AFTER_SECONDS:
                return {**answer, "status": "failed_recently"}
            if not apify_key():
                return {**answer, "status": "no_key"}
            spent = spent_today(spend_file, today)
            if spent + WORST_COST_USD > DAILY_LIMIT_USD + 1e-9:
                return {**answer, "status": "over_budget"}
            _write_spend(spend_file, today, spent + WORST_COST_USD)  # before: a crash can't hide spending
            report = None
            own_client = client is None
            client = client or httpx.Client(timeout=FETCH_TIMEOUT_SECONDS + 15)
            try:
                report = import_doctors(city, locality, [specialty], FETCH_PER_SEARCH, WORST_COST_USD, path,
                                        client=client, run_timeout=FETCH_TIMEOUT_SECONDS, workers=len(GENDERS))
            except ProviderImportError:  # the list could not be read or saved: cost unknown, worst case kept
                pass
            finally:
                if own_client:
                    client.close()
            if report is not None:
                _write_spend(spend_file, today, spent + report["cost_usd"])
            if report is None or specialty not in report["searched"]:
                _failed_at[key] = time.monotonic()
                return {**answer, "status": "failed", "results": report["results"] if report else 0,
                        "cost_usd": report["cost_usd"] if report else WORST_COST_USD}
            _failed_at.pop(key, None)
            return {**answer, "status": "fetched", "results": report["results"], "cost_usd": report["cost_usd"]}
    except (OSError, ProviderImportError):  # the list or the spending record can't be read or written
        return {**answer, "status": "failed"}
