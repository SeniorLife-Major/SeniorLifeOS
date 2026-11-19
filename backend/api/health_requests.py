"""Doctor search (fastapi-backend Phase 2): ask for a doctor in your own words, answer the one
question it may ask, and see the result again for 30 days (storage/health_service.py).

The AI model always reads the request, and the doctor list may be refreshed for it (the owner's
choice, frontend Decision 73: doctor search needs the model). The saved consents in the profile
are for documents only. A request can still say useLlm / medicalCloudConsent false (tests, API
callers); the app never does. The person's name is hidden from the model. The phone's location is used for this search only and never stored. The home saved in the
profile is used when the person means home ("ghar ke paas"); the result calls it "your home".

With "Accept: text/event-stream" the answer comes as server-sent events: one "step" event per
step finished ({"step": "safety_check"}, ...), then one "result" event (the same envelope as the
JSON answer) or one "error" event. The search runs in a worker thread, so it finishes and is
saved even if the caller goes away; GET /api/v1/health-requests/{id} shows it then.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from queue import Queue
from typing import Annotated, Any, Iterator

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import Field, StrictBool, model_validator

from backend.api.deps import Actor, get_account_service, get_health_service
from backend.common.request_id import in_this_request
from backend.common.schemas import ApiResponse, CamelModel
from backend.core.config import get_config
from backend.errors import AppException
from backend.graphs.health_graph import MAX_QUERY_CHARS
from backend.tools.specialties import SPECIALTIES

router = APIRouter(prefix="/api/v1", tags=["Doctor search"])
logger = logging.getLogger(__name__)

STREAM_WORKERS = 4  # searches streamed at the same time; more wait their turn
_runner = ThreadPoolExecutor(max_workers=STREAM_WORKERS, thread_name_prefix="health-request")


# ---- DTOs ---------------------------------------------------------------------------------

class LocationDto(CamelModel):
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lon: float = Field(ge=-180, le=180, allow_inf_nan=False)


class HealthRequestCreate(CamelModel):
    query: str = Field(min_length=1, max_length=MAX_QUERY_CHARS)  # in the person's own words
    current_location: LocationDto | None = None  # the phone's, shared with permission
    use_llm: StrictBool | None = None  # None: yes (doctor search always uses the model)
    medical_cloud_consent: StrictBool | None = None  # None: yes


class HealthAnswer(CamelModel):
    """Exactly one, the one the question asks for."""

    call_emergency: StrictBool | None = None
    clarification: str | None = Field(default=None, min_length=1, max_length=500)
    location: Annotated[str, Field(min_length=1, max_length=200)] | LocationDto | None = None

    @model_validator(mode="after")
    def exactly_one(self) -> HealthAnswer:
        if sum(value is not None for value in (self.call_emergency, self.clarification, self.location)) != 1:
            raise ValueError("Send exactly one of callEmergency, clarification or location")
        return self

    def kind_and_value(self) -> tuple[str, Any]:
        if self.call_emergency is not None:
            return "call_emergency", self.call_emergency
        if self.clarification is not None:
            return "clarification", self.clarification
        location = self.location
        return "location", location.model_dump() if isinstance(location, LocationDto) else location


class SpecialtyDto(CamelModel):
    key: str
    label: str


class PlaceDto(CamelModel):
    label: str | None = None
    source: str | None = None  # phone, request or answer


class ClinicDto(CamelModel):
    name: str | None = None
    locality: str | None = None
    address: str | None = None
    city: str | None = None
    hours: str | None = None
    lat: float | None = None
    lon: float | None = None


class DoctorDto(CamelModel):
    doctor_id: str | None = None
    name: str | None = None
    gender: str | None = None
    specialty: str | None = None
    qualifications: str | None = None
    experience_years: int | None = None
    languages: list[str] = []
    fee: int | None = None
    clinic: ClinicDto
    distance_km: float | None = None
    registration_level: str | None = None
    registration_note: str | None = None
    recommend_percent: int | None = None
    patient_stories: int | None = None
    score: float | None = None
    held_back: str | None = None
    reasons: list[str] = []
    labels: list[str] = []
    flags: list[str] = []
    profile_url: str | None = None
    fetched_on: str | None = None
    stale: bool = False


class EmergencyNumberDto(CamelModel):
    number: str
    for_: str = Field(alias="for")


class EmergencyDto(CamelModel):
    numbers: list[EmergencyNumberDto]
    message: str | None = None
    red_flags: list[str] = []
    model_signs: list[str] = []
    source: str | None = None  # rules, rules+model, model, person; None while it asks


class HealthResultDto(CamelModel):
    request_id: str
    status: str
    specialty: SpecialtyDto | None = None
    doctor_fallback: str | None = None
    search_location: PlaceDto | None = None
    search_radius_km: int | None = None
    doctors: list[DoctorDto] = []  # the ones shown, with reasons
    other_doctors: list[DoctorDto] = []  # the rest, best first
    is_top_choice: bool = False
    answer: str | None = None  # the whole reply as plain text
    notes: list[str] = []
    attribution: str | None = None  # must be shown with a place found by place search
    question: str | None = None
    options: list[str] = []
    emergency: EmergencyDto | None = None


class HealthRequestSummaryDto(CamelModel):
    request_id: str
    status: str
    specialty: SpecialtyDto | None = None
    created_at: datetime


# ---- helpers ------------------------------------------------------------------------------

Health = Annotated[Any, Depends(get_health_service)]
Accounts = Annotated[Any, Depends(get_account_service)]


def envelope(data: Any, response: Response | None = None) -> ApiResponse[Any]:
    if response is not None:
        response.headers["Cache-Control"] = "no-store"  # health details
    return ApiResponse.of(data=data, version=get_config().app.version)


def wants_stream(request: Request) -> bool:
    return "text/event-stream" in request.headers.get("accept", "").lower()


def final_result(events: Iterator[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, data in events:
        if name == "result":
            result = data
    return result


def event_text(name: str, data: dict[str, Any]) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def stream(events: Iterator[tuple[str, dict[str, Any]]]) -> Iterator[str]:
    """Runs the search in a worker thread, so it finishes and is saved even if the caller goes
    away; this only passes its events on."""
    queue: Queue[str | None] = Queue()

    def work() -> None:
        try:
            for name, data in events:
                if name == "result":
                    data = envelope(HealthResultDto.model_validate(data)).model_dump(mode="json", by_alias=True)
                queue.put(event_text(name, data))
        except AppException as exc:
            code = getattr(exc.error_code, "value", exc.error_code)
            queue.put(event_text("error", {"status": "ERROR", "error": {"code": code, "message": exc.message}}))
        except Exception as exc:
            logger.error("A streamed health request failed: %s", type(exc).__name__)  # never the message
            queue.put(event_text("error", {"status": "ERROR", "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "An unexpected internal server error occurred. Please try again later."}}))
        finally:
            queue.put(None)

    _runner.submit(in_this_request(work))  # its log lines keep the request's ID
    while (text := queue.get()) is not None:
        yield text


def respond(events: Iterator[tuple[str, dict[str, Any]]], request: Request, response: Response,
            status_code: int) -> Any:
    if wants_stream(request):
        return StreamingResponse(stream(events), status_code=status_code, media_type="text/event-stream",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
    response.status_code = status_code
    return envelope(HealthResultDto.model_validate(final_result(events)), response)


# ---- routes -------------------------------------------------------------------------------

@router.post("/health-requests", status_code=status.HTTP_201_CREATED, response_model=ApiResponse[HealthResultDto],
             summary="Find a doctor: describe the need in your own words")
def create_health_request(body: HealthRequestCreate, request: Request, response: Response, actor: Actor,
                          health: Health, accounts: Accounts) -> Any:
    saved = accounts.saved_choices(actor)  # the home and the name; its consents are for documents
    events = health.start(
        actor, body.query,
        current_location=body.current_location.model_dump() if body.current_location else None,
        home_location=saved["home"],
        use_llm=True if body.use_llm is None else body.use_llm,
        medical_cloud_consent=True if body.medical_cloud_consent is None else body.medical_cloud_consent,
        owner_name=saved["display_name"])
    return respond(events, request, response, status.HTTP_201_CREATED)


@router.post("/health-requests/{request_id}/answer", response_model=ApiResponse[HealthResultDto],
             summary="Answer the question a doctor search asked")
def answer_health_request(request_id: str, body: HealthAnswer, request: Request, response: Response, actor: Actor,
                          health: Health) -> Any:
    kind, value = body.kind_and_value()
    return respond(health.answer(actor, request_id, kind, value), request, response, status.HTTP_200_OK)


@router.get("/health-requests/{request_id}", response_model=ApiResponse[HealthResultDto],
            summary="A doctor search and its result")
def get_health_request(request_id: str, response: Response, actor: Actor,
                       health: Health) -> ApiResponse[HealthResultDto]:
    return envelope(HealthResultDto.model_validate(health.get(actor, request_id)), response)


@router.get("/health-requests", response_model=ApiResponse[list[HealthRequestSummaryDto]],
            summary="My doctor searches from the last 30 days, newest first")
def list_health_requests(response: Response, actor: Actor, health: Health,
                         limit: Annotated[int, Query(ge=1, le=50)] = 20) -> ApiResponse[list[HealthRequestSummaryDto]]:
    return envelope([HealthRequestSummaryDto.model_validate(item) for item in health.list(actor, limit)], response)


@router.get("/health/specialties", response_model=ApiResponse[list[SpecialtyDto]],
            summary="The kinds of doctor doctor search knows")
def list_specialties(response: Response, actor: Actor) -> ApiResponse[list[SpecialtyDto]]:
    return envelope([SpecialtyDto(key=key, label=label) for key, label in SPECIALTIES.items()], response)
