"""Where to look for a doctor: a place in words turned into coordinates (Geoapify), or the
coordinates the phone sends (doctor search, step 2a).

Geoapify's geocoder (OpenStreetMap data) answers "Andheri West, Mumbai" with coordinates. Seen
live (doctor-search Decision 8): "Andheri" alone comes back first as a village in Punjab, with
full confidence, and Mumbai's Andheri only second. Confidence cannot catch that, so answers far
apart from each other count as different places: if more than one is left, the person is asked
which one (unless one is near where the phone says they are).

Privacy: Geoapify gets only the place text (identifiers removed), never the request, the need or a
name. Coordinates are kept to 3 decimals (about 100 m). The phone's location goes to Geoapify
only rounded to 2 decimals (about 1 km, doctor-search Decision 14): as a hint when a named place
is looked up (answers near the person first), and in area_near, to name the area around the
person when doctors must be fetched for it.

Geoapify takes its key only in the URL (`apiKey=`), unlike the project's other services. So the
key is hidden in httpx's request log lines, errors name only the error type, and the original
exception (which holds the URL) is not chained onto ours.
"""

from __future__ import annotations

import copy
import logging
import math
import os
import re
import threading
import time
from typing import Any

import httpx

from ..utils.env import find_env_file, load_env_file
from ..utils.privacy import redact
from ..utils.rate_limit import CLOUD_RATE_SETTING, DEFAULT_CLOUD_PER_MINUTE, rate_limiter

GEOCODE_URL = "https://api.geoapify.com/v1/geocode/search"
REVERSE_URL = "https://api.geoapify.com/v1/geocode/reverse"
PLACES_URL = "https://api.geoapify.com/v2/places"
KEY_NAME = "GEOAPIFY_API_KEY"
SERVICE = "geoapify"
ATTRIBUTION = "© OpenStreetMap contributors, powered by Geoapify"  # must be shown with the place
TIMEOUT_SECONDS = 15
BUSY_STATUSES = {429, 503}
MAX_RETRIES = 1
RESULTS_ASKED = 5  # one credit whatever the number; more answers show whether a name is ambiguous
MIN_CONFIDENCE = 0.5  # seen live: "Andheri West, Mumbai" 0.67; wrong guesses come back as 0
SAME_PLACE_KM = 25  # answers closer than this are the same place
NEAR_PERSON_KM = 50  # a match this close to the phone's location is the one meant
MAX_PLACE_CHARS = 200
COORD_DECIMALS = 3  # about 100 m
AREA_DECIMALS = 2  # about 1 km: how precisely the phone's location ever leaves the machine
# The area around a point: the nearest suburb, town or village (seen live: "Andheri West",
# "Koramangala" — the names Practo uses; reverse geocoding gave wards such as "K/W Ward").
AREA_CATEGORIES = "populated_place.suburb,populated_place.town,populated_place.village"
AREA_RADIUS_M = 3000
INDIA_BOX = (6.0, 37.6, 68.0, 97.5)  # south, north, west, east: a rough box around India
WIDE_AREA_TYPES = {"suburb", "district", "city", "county", "postcode", "state"}  # preferred as a label
AREA_TYPES = {"suburb", "district"}  # the answer's own name is the neighbourhood
WHOLE_CITY_TYPES = {"city", "county", "state", "country"}  # no neighbourhood
MAX_NAME_CHARS = 80
PLACEHOLDER = re.compile(r"\[[A-Z_]+_\d+\]")
KEY_IN_URL = re.compile(r"(apiKey=)[^&\s\"']+")
CACHE_SIZE = 256

# Where the person wants the doctor, from their own words (used when the AI model is not).
HOME_WORDS = [re.compile(words, re.IGNORECASE) for words in (
    r"\b(?:near|close\s+to|around)\s+(?:my|our|the)\s+(?:home|house)\b",
    r"\bghar\s+(?:ke|ki|se)\s+(?:paas|pass|nazdeek|najdeek|najdik|kareeb|karib)", "घर के पास", "घर के नज़दीक",
)]
HERE_WORDS = [re.compile(words, re.IGNORECASE) for words in (
    r"\bnear\s*(?:by|me|here)\b", r"\b(?:around|close\s+to)\s+(?:me|here)\b", r"\bwhere\s+I\s+am\b",
    r"\b(?:mere|mujhse|hamare|yahan|yaha|idhar)\s+(?:paas|pass|nazdeek|najdeek|najdik|kareeb|karib)",
    r"\baas\s*paas\b", r"\byahin\b", "मेरे पास", "यहां", "यहाँ", "आसपास", "आस पास",
)]


class GeocodeError(RuntimeError):
    """The place search could not be used (no key, network, service error). Never holds the key."""


class _HideKey(logging.Filter):
    """httpx logs every request URL at INFO level; Geoapify's has the key in it."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if "apiKey=" in message:
            record.msg, record.args = KEY_IN_URL.sub(r"\1[hidden]", message), ()
        return True


logging.getLogger("httpx").addFilter(_HideKey())

_cache: dict[tuple[str, tuple[float, float] | None], dict[str, Any]] = {}
_cache_lock = threading.Lock()


def geoapify_key() -> str | None:
    load_env_file(find_env_file())
    return os.environ.get(KEY_NAME, "").strip() or None


def search_near_from_text(text: str) -> str:
    """Where the person wants the doctor, from their words: "home", "current_location", or
    "unknown" (a place may be named; this machine can't tell, so the person will be asked)."""
    text = text or ""
    if any(pattern.search(text) for pattern in HOME_WORDS):
        return "home"
    if any(pattern.search(text) for pattern in HERE_WORDS):
        return "current_location"
    return "unknown"


def in_india(lat: float, lon: float) -> bool:
    south, north, west, east = INDIA_BOX
    return south <= lat <= north and west <= lon <= east


def device_location(value: Any) -> dict[str, float] | None:
    """The phone's location ({"lat": .., "lon": ..}), rounded; None if missing, malformed or
    outside India (only Indian doctors are listed)."""
    if not isinstance(value, dict):
        return None
    lat, lon = value.get("lat"), value.get("lon")
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (lat, lon)):
        return None
    if not in_india(lat, lon):
        return None
    return {"lat": round(lat, COORD_DECIMALS), "lon": round(lon, COORD_DECIMALS)}


def distance_km(a: dict[str, float], b: dict[str, float]) -> float:
    """Straight-line distance between two {"lat", "lon"} points (haversine)."""
    lat1, lon1, lat2, lon2 = map(math.radians, (a["lat"], a["lon"], b["lat"], b["lon"]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def clean_place_text(text: str) -> str:
    """The place text as sent: identifiers (phone, Aadhaar, ...) removed, spaces tidied, capped."""
    safe, _ = redact(text or "")
    return " ".join(PLACEHOLDER.sub(" ", safe).split())[:MAX_PLACE_CHARS].strip(" ,")


def place_label(result: dict[str, Any]) -> str:
    parts: list[str] = []
    for part in (result.get("name") or result.get("address_line1"), result.get("suburb"), result.get("city"),
                 result.get("state")):
        if isinstance(part, str) and part.strip() and part.strip() not in parts:
            parts.append(part.strip())
    # "Andheri" adds nothing next to "Andheri West" (seen live: "Andheri, Andheri West, Mumbai").
    parts = [part for part in parts
             if not any(other.casefold().startswith(part.casefold() + " ") for other in parts)]
    if parts:
        return ", ".join(parts)
    return str(result.get("formatted") or "").removesuffix(", India")


def _name(value: Any) -> str | None:
    return value.strip()[:MAX_NAME_CHARS] if isinstance(value, str) and value.strip() else None


def city_and_area(result: dict[str, Any]) -> tuple[str | None, str | None]:
    """The city and the neighbourhood of an answer (doctors are searched by these names); no
    neighbourhood when the answer is a whole city or larger."""
    kind = result.get("result_type")
    if kind in WHOLE_CITY_TYPES:
        city, name = _name(result.get("city")), _name(result.get("name"))
        if kind == "city" and city and name and name.casefold() != city.casefold():
            return city, name  # a "city" inside a city is a neighbourhood (seen live: Koregaon Park, Pune)
        return city or name, None
    area = _name(result.get("name")) if kind in AREA_TYPES else None
    return _name(result.get("city")), area or _name(result.get("suburb")) or _name(result.get("district"))


def _place(result: dict[str, Any]) -> dict[str, Any]:
    city, area = city_and_area(result)
    return {"lat": round(float(result["lat"]), COORD_DECIMALS), "lon": round(float(result["lon"]), COORD_DECIMALS),
            "label": place_label(result), "city": city, "area": area, "attribution": ATTRIBUTION}


def _confident(result: dict[str, Any]) -> bool:
    try:
        confidence = float((result.get("rank") or {}).get("confidence") or 0)
        lat, lon = float(result["lat"]), float(result["lon"])
    except (TypeError, ValueError, KeyError):
        return False
    return confidence >= MIN_CONFIDENCE and in_india(lat, lon)


def group_places(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One place per group of answers within SAME_PLACE_KM of each other, best-ranked group
    first. A group is named by its first area-type answer (suburb, city...) rather than a
    building or a shop that happens to carry the name."""
    groups: list[list[dict[str, Any]]] = []
    for result in filter(_confident, results):
        point = {"lat": float(result["lat"]), "lon": float(result["lon"])}
        for group in groups:
            if distance_km(point, {"lat": float(group[0]["lat"]), "lon": float(group[0]["lon"])}) < SAME_PLACE_KM:
                group.append(result)
                break
        else:
            groups.append([result])
    places: list[dict[str, Any]] = []
    for group in groups:
        place = _place(next((r for r in group if r.get("result_type") in WIDE_AREA_TYPES), group[0]))
        # Places the person couldn't tell apart by name (seen live: two "Rampur, Shahdol, Madhya
        # Pradesh" 38 km apart): only the best-ranked is kept.
        if all(place["label"].casefold() != kept["label"].casefold() for kept in places):
            places.append(place)
    return places


def _get_with_retry(client: httpx.Client, params: dict[str, Any], url: str = GEOCODE_URL) -> httpx.Response:
    """GET, once more after a short wait if the service says it is busy (429 or 503). Every
    attempt waits its turn under the per-minute limit (daily credits are limited)."""
    try:
        limiter = rate_limiter(SERVICE, CLOUD_RATE_SETTING, DEFAULT_CLOUD_PER_MINUTE)
    except RuntimeError as exc:  # a bad setting: nothing is sent
        raise GeocodeError(str(exc)) from None
    for attempt in range(MAX_RETRIES + 1):
        limiter.acquire()
        response = client.get(url, params=params)
        if response.status_code not in BUSY_STATUSES or attempt == MAX_RETRIES:
            return response
        time.sleep(2.0 * (attempt + 1))
    return response


def find_place(text: str, near: dict[str, float] | None = None,
               client: httpx.Client | None = None) -> dict[str, Any]:
    """Look up a place in India by name.

    Returns {"status": "found", "place": {lat, lon, label, city, area, attribution}},
    {"status": "ambiguous", "options": [place, ...]} (far-apart places share the name), or
    {"status": "not_found"}. near: the phone's location, if known: answers near it come first,
    and one within NEAR_PERSON_KM settles an ambiguous name.
    Raises GeocodeError if the service can't be used; the caller asks the person instead.
    """
    place_text = clean_place_text(text)
    if not place_text:
        return {"status": "not_found"}
    near = device_location(near)
    cache_key = (place_text.casefold(), (near["lat"], near["lon"]) if near else None)
    with _cache_lock:
        if cache_key in _cache:
            return copy.deepcopy(_cache[cache_key])
    key = geoapify_key()
    if not key:
        raise GeocodeError(f"Place search needs {KEY_NAME}")
    params: dict[str, Any] = {"text": place_text, "filter": "countrycode:in", "format": "json",
                              "limit": RESULTS_ASKED, "apiKey": key}
    if near:  # about 1 km is enough to rank nearby answers first
        params["bias"] = f"proximity:{round(near['lon'], AREA_DECIMALS)},{round(near['lat'], AREA_DECIMALS)}"
    own_client = client is None
    client = client or httpx.Client(timeout=TIMEOUT_SECONDS)
    try:
        response = _get_with_retry(client, params)
    except httpx.HTTPError as exc:  # its message or request may hold the URL, so it is not chained
        raise GeocodeError(f"Place search could not be reached ({type(exc).__name__})") from None
    finally:
        if own_client:
            client.close()
    if response.status_code != 200:
        raise GeocodeError(f"Place search failed (HTTP {response.status_code})")  # body not shown
    try:
        results = response.json()["results"]
        if not isinstance(results, list):
            raise TypeError
    except (ValueError, KeyError, TypeError):
        raise GeocodeError("Place search sent an answer that could not be read") from None
    places = group_places([r for r in results if isinstance(r, dict)])
    if near and places:
        closest = min(places, key=lambda place: distance_km(place, near))
        if distance_km(closest, near) <= NEAR_PERSON_KM:
            places = [closest]
    if not places:
        answer: dict[str, Any] = {"status": "not_found"}
    elif len(places) == 1:
        answer = {"status": "found", "place": places[0]}
    else:
        answer = {"status": "ambiguous", "options": places[:3]}
    with _cache_lock:
        if len(_cache) >= CACHE_SIZE:
            _cache.pop(next(iter(_cache)))
        _cache[cache_key] = answer
    return copy.deepcopy(answer)


def _get_json(client: httpx.Client, url: str, params: dict[str, Any]) -> dict[str, Any]:
    """One area lookup, as JSON. Errors name only their type or status (the URL holds the key)."""
    try:
        response = _get_with_retry(client, params, url)
    except httpx.HTTPError as exc:
        raise GeocodeError(f"Area lookup could not be reached ({type(exc).__name__})") from None
    if response.status_code != 200:
        raise GeocodeError(f"Area lookup failed (HTTP {response.status_code})")  # body not shown
    try:
        answer = response.json()
    except ValueError:
        raise GeocodeError("Area lookup sent an answer that could not be read") from None
    if not isinstance(answer, dict):
        raise GeocodeError("Area lookup sent an answer that could not be read")
    return answer


def _first(items: Any, field: str | None = None) -> dict[str, Any]:
    first = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else {}
    inner = first.get(field) if field else first
    return inner if isinstance(inner, dict) else {}


def area_near(point: Any, client: httpx.Client | None = None) -> dict[str, str | None] | None:
    """The city and neighbourhood around the phone's location, to fetch doctors there:
    {"city", "area"} from the nearest suburb, town or village within AREA_RADIUS_M, else the city
    alone ("area": None); None if not known. Only the point rounded to AREA_DECIMALS (about 1 km)
    is sent. Raises GeocodeError if the service can't be used."""
    here = device_location(point)
    if here is None:
        return None
    lat, lon = round(here["lat"], AREA_DECIMALS), round(here["lon"], AREA_DECIMALS)
    cache_key = ("area_near", lat, lon)
    with _cache_lock:
        if cache_key in _cache:
            return copy.deepcopy(_cache[cache_key])
    key = geoapify_key()
    if not key:
        raise GeocodeError(f"Area lookup needs {KEY_NAME}")
    own_client = client is None
    client = client or httpx.Client(timeout=TIMEOUT_SECONDS)
    try:
        places = _get_json(client, PLACES_URL, {"categories": AREA_CATEGORIES, "limit": 1, "apiKey": key,
                                                "filter": f"circle:{lon},{lat},{AREA_RADIUS_M}",
                                                "bias": f"proximity:{lon},{lat}"})
        nearest = _first(places.get("features"), "properties")
        city, area = _name(nearest.get("city")), _name(nearest.get("name"))
        if not city:  # no suburb, town or village this near: the city alone
            reverse = _get_json(client, REVERSE_URL, {"lat": lat, "lon": lon, "format": "json", "limit": 1,
                                                      "apiKey": key})
            city = _name(_first(reverse.get("results")).get("city"))
    finally:
        if own_client:
            client.close()
    answer = None
    if city:
        answer = {"city": city, "area": area if area and area.casefold() != city.casefold() else None}
    with _cache_lock:
        if len(_cache) >= CACHE_SIZE:
            _cache.pop(next(iter(_cache)))
        _cache[cache_key] = answer
    return copy.deepcopy(answer)
