"""The signed-in person's profile (fastapi-backend Phase 1): language, home, accessibility and
consent choices, kept between requests.

Home is kept rounded to about 100 m (tools/geocode.COORD_DECIMALS). It is set from a place name
(looked up with the same place search the doctor search uses) or from the phone's location. A
name that fits far-apart places comes back as a list to choose from; the app then sends the
chosen one's lat and lon.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response
from pydantic import Field, StrictBool

from backend.api.deps import Actor, get_account_service
from backend.common.schemas import ApiResponse, CamelModel
from backend.core.config import get_config
from backend.errors import AppException, ValidationException
from backend.tools.geocode import GeocodeError, clean_place_text, device_location, find_place

router = APIRouter(prefix="/api/v1/me", tags=["Profile"])
logger = logging.getLogger(__name__)


class PlaceSearchUnavailable(AppException):
    def __init__(self) -> None:
        super().__init__(message="Place search is not available right now; share your location instead",
                         error_code="PLACE_SEARCH_UNAVAILABLE", status_code=503)


# ---- DTOs ---------------------------------------------------------------------------------

class HomeDto(CamelModel):
    label: str | None = None
    lat: float
    lon: float


class AccessibilityDto(CamelModel):
    font_scale: float
    high_contrast: bool
    voice: bool


class ConsentsDto(CamelModel):
    use_llm: bool  # an AI model may read what the person types
    medical_cloud: bool  # health questions may go to a cloud AI model
    cloud_ocr: bool  # document photos may be read by a cloud service


class ProfileDto(CamelModel):
    language: str
    home_location: HomeDto | None = None
    accessibility: AccessibilityDto
    consents: ConsentsDto
    consents_updated_at: datetime | None = None


class HomeRequest(CamelModel):
    place: str | None = Field(default=None, max_length=200)  # "Andheri West, Mumbai"
    lat: float | None = None  # or the phone's location
    lon: float | None = None


class AccessibilityRequest(CamelModel):
    font_scale: float | None = Field(default=None, ge=0.8, le=2.0)
    high_contrast: StrictBool | None = None
    voice: StrictBool | None = None


class ConsentsRequest(CamelModel):
    use_llm: StrictBool | None = None
    medical_cloud: StrictBool | None = None
    cloud_ocr: StrictBool | None = None


class ProfileUpdateRequest(CamelModel):
    """Only what is sent changes. "homeLocation": null forgets the home."""

    language: str | None = Field(default=None, max_length=8)
    home_location: HomeRequest | None = None
    accessibility: AccessibilityRequest | None = None
    consents: ConsentsRequest | None = None


# ---- helpers ------------------------------------------------------------------------------

Accounts = Annotated[Any, Depends(get_account_service)]


def envelope(data: Any, response: Response) -> ApiResponse[Any]:
    response.headers["Cache-Control"] = "no-store"  # personal
    return ApiResponse.of(data=data, version=get_config().app.version)


def resolve_home(request: HomeRequest) -> dict[str, Any]:
    """{"label", "lat", "lon"}, rounded to about 100 m, or a 422 / 503 the app can act on."""
    place = clean_place_text(request.place or "")
    if request.lat is not None or request.lon is not None:
        point = device_location({"lat": request.lat, "lon": request.lon})
        if point is None:
            raise ValidationException("The home location must be a point in India (lat and lon)")
        return {"label": place or None, **point}
    if not place:
        raise ValidationException("Send the home as a place name, or as lat and lon")
    try:
        answer = find_place(place)
    except GeocodeError as exc:
        logger.warning("Place search failed for a home location: %s", exc)  # the message never holds the place
        raise PlaceSearchUnavailable() from None
    if answer["status"] == "found":
        found = answer["place"]
        return {"label": found["label"], "lat": found["lat"], "lon": found["lon"]}
    if answer["status"] == "ambiguous":
        options = [{"label": option["label"], "lat": option["lat"], "lon": option["lon"]}
                   for option in answer["options"]]
        raise ValidationException("This name fits more than one place; choose one and send its lat and lon",
                                  details={"options": options})
    raise ValidationException("This place was not found; add the city, or share your location")


# ---- routes -------------------------------------------------------------------------------

@router.get("/profile", response_model=ApiResponse[ProfileDto], summary="My profile")
def get_profile(response: Response, actor: Actor, accounts: Accounts) -> ApiResponse[ProfileDto]:
    return envelope(ProfileDto.model_validate(accounts.profile(actor)), response)


@router.patch("/profile", response_model=ApiResponse[ProfileDto], summary="Change my profile")
def update_profile(body: ProfileUpdateRequest, response: Response, actor: Actor,
                   accounts: Accounts) -> ApiResponse[ProfileDto]:
    changes: dict[str, Any] = {}
    if body.language is not None:
        changes["language"] = body.language
    if "home_location" in body.model_fields_set:
        changes["home"] = resolve_home(body.home_location) if body.home_location is not None else None
    if body.accessibility is not None:
        changes["accessibility"] = body.accessibility.model_dump(exclude_none=True)
    if body.consents is not None:
        changes["consents"] = body.consents.model_dump(exclude_none=True)
    return envelope(ProfileDto.model_validate(accounts.update_profile(actor, changes)), response)
