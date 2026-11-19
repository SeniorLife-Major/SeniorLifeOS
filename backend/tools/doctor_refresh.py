"""Ask Practo (through Apify) for doctors live, on every search, while the person waits
(doctor-search Decision 22; it replaced the 30-day cache of Decision 13).

Each search fetches its area and specialty now: one Apify search per gender, both at the same
time, FETCH_PER_SEARCH doctors each (at most WORST_COST_USD), saved into the stored list like an
import. The answer names the doctors this fetch brought (doctor_ids), so the search can show live
doctors only. The stored list is the backup: when the fetch can't be made (no key, no area name,
Practo failed, the day's budget is used) the search shows saved doctors and says so
(graphs/health_graph.py).

Not paid for twice: a search fetched less than REUSE_SECONDS ago (a second request for the same
thing while the first was fetching, a double tap) is answered with that fetch ("reused"), and a
search that failed is not tried again for RETRY_AFTER_SECONDS.

Privacy: Apify learns the specialty and the area name, never coordinates. For "near me", Geoapify
names the area from the phone's location rounded to about 1 km (tools/geocode.area_near,
Decision 14). The name never comes from a stored clinic: Practo spells its clinics' areas its own
way (seen live, Decision 15).

Spending: at most daily_limit_usd() a day (DOCTOR_FETCH_DAILY_USD, default $2.00, about 40
searches). The worst case is written down before each fetch (var/providers/fetch_spend.json) and
corrected after, so a crash can't hide spending. Fetches run one at a time, so the budget check
and the record can't race.
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

from .geocode import GeocodeError, area_near
from .provider_import import (
    DEFAULT_LIST, GENDERS, PRICE_PER_RESULT, ProviderImportError, apify_key, import_doctors, search_key, today_utc,
)

FETCH_PER_SEARCH = 10
WORST_COST_USD = round(len(GENDERS) * FETCH_PER_SEARCH * PRICE_PER_RESULT, 4)  # $0.05
DAILY_LIMIT_SETTING = "DOCTOR_FETCH_DAILY_USD"
DEFAULT_DAILY_LIMIT_USD = 2.00
FETCH_TIMEOUT_SECONDS = 60  # per Apify run; the person is waiting
RETRY_AFTER_SECONDS = 30 * 60
REUSE_SECONDS = 60
SPEND_FILE = DEFAULT_LIST.parent / "fetch_spend.json"
LIVE = ("fetched", "reused")  # statuses whose doctor_ids came from Practo just now

_lock = threading.Lock()
_failed_at: dict[str, float] = {}  # search key -> when it last failed (time.monotonic)
_fetched: dict[str, tuple[float, list[str]]] = {}  # search key -> (when, the doctors it brought)


def daily_limit_usd() -> float:
    """The day's budget for live fetches, from DOCTOR_FETCH_DAILY_USD; the default if it is not a
    number of dollars from 0 up."""
    try:
        limit = float(os.environ.get(DAILY_LIMIT_SETTING, "").strip())
    except ValueError:
        return DEFAULT_DAILY_LIMIT_USD
    return limit if 0 <= limit < float("inf") else DEFAULT_DAILY_LIMIT_USD


def area_of(place: dict[str, Any]) -> tuple[str, str | None] | None:
    """(city, neighbourhood or None) to search Practo by, or None if not known. A looked-up place
    has its own names (tools/geocode.py). For the phone's location: the nearest suburb, town or
    village Geoapify knows (the location rounded to about 1 km), else the city alone. Always the
    same source, so the same spot gets the same name."""
    city, area = place.get("city"), place.get("area")
    if isinstance(city, str) and city.strip():
        return city.strip(), (area.strip() if isinstance(area, str) and area.strip() else None)
    try:
        found = area_near(place)
    except GeocodeError:  # no key, network, service error: not fetched this time
        return None
    return (found["city"], found["area"]) if found else None


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
        return daily_limit_usd()
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


def refresh_doctors(place: dict[str, Any], specialty: str, path: Path | None = None,
                    spend_file: Path | None = None, today: date | None = None,
                    client: httpx.Client | None = None) -> dict[str, Any]:
    """Fetch this search from Practo now.

    {"status": ..., "area": "Koregaon Park, Pune" or None, "results": n, "cost_usd": x,
    "doctor_ids": [...]}; status is fetched or reused (doctor_ids: who Practo listed, live), or
    no_area, no_key, over_budget, failed or failed_recently (doctor_ids empty: show saved ones)."""
    path, spend_file, today = Path(path or DEFAULT_LIST), Path(spend_file or SPEND_FILE), today or today_utc()
    found = area_of(place)
    if found is None:
        return {"status": "no_area", "area": None, "results": 0, "cost_usd": 0.0, "doctor_ids": []}
    city, locality = found
    key = search_key(city, locality, specialty)
    answer = {"area": ", ".join(part for part in (locality, city) if part), "results": 0, "cost_usd": 0.0,
              "doctor_ids": []}
    try:
        with _lock:
            fetched = _fetched.get(key)
            if fetched and time.monotonic() - fetched[0] < REUSE_SECONDS:  # fetched just now for another request
                return {**answer, "status": "reused", "doctor_ids": list(fetched[1])}
            if time.monotonic() - _failed_at.get(key, float("-inf")) < RETRY_AFTER_SECONDS:
                return {**answer, "status": "failed_recently"}
            if not apify_key():
                return {**answer, "status": "no_key"}
            spent = spent_today(spend_file, today)
            if spent + WORST_COST_USD > daily_limit_usd() + 1e-9:
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
            _fetched[key] = (time.monotonic(), list(report["doctor_ids"]))
            return {**answer, "status": "fetched", "results": report["results"], "cost_usd": report["cost_usd"],
                    "doctor_ids": list(report["doctor_ids"])}
    except (OSError, ProviderImportError):  # the list or the spending record can't be read or written
        return {**answer, "status": "failed"}
