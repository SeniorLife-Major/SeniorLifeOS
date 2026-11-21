"""Benefit schemes (benefits Part 2): which made-up demo schemes my papers may get me, and the
application package for one (storage/benefits_service.py, graphs/benefits_graph.py).

- GET  /api/v1/benefits/schemes: the demo schemes and what each asks for.
- POST /api/v1/benefits/checks: {} screens every scheme against my confirmed papers;
  {"schemeId": "widow_pension", "declarations": {"not_remarried": true}} checks one and, when it
  passes, drafts the package ("awaiting_review").
- POST /api/v1/benefits/checks/{id}/approve: {"actionId"} approves that package, if my papers
  have not changed since ("ready_for_manual_handoff"); 409 when they have.
- GET  /api/v1/benefits/checks[/{id}]: my checks from the last 30 days.

Nothing is sent anywhere: no government site is asked and no application is submitted. The
schemes are made up for the demonstration and are not advice.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import Field, StrictBool

from backend.api.deps import Actor, get_account_service, get_benefits_service
from backend.common.schemas import ApiResponse, CamelModel
from backend.core.config import get_config

router = APIRouter(prefix="/api/v1/benefits", tags=["Benefit schemes"])

SchemeStatus = Literal["likely", "add_documents", "not_eligible", "please_check"]
CriterionStatus = Literal["met", "unmet", "unknown", "conflict"]
NeedStatus = Literal["usable", "unconfirmed", "expired", "missing"]


# ---- DTOs ---------------------------------------------------------------------------------

class CriterionLabelDto(CamelModel):
    criterion_id: str
    label: str


class DeclarationDto(CamelModel):
    key: str
    label: str


class SchemeDto(CamelModel):
    scheme_id: str
    name: str
    summary: str | None = None
    benefit: str | None = None
    exclusive_group: str | None = None  # schemes of which only one can be had
    required_documents: list[str]
    criteria: list[CriterionLabelDto]
    declarations: list[DeclarationDto] = []  # what no paper shows; the person says it
    apply_at: str | None = None


class CheckRequest(CamelModel):
    scheme_id: str | None = Field(default=None, max_length=64)  # none: screen every scheme
    declarations: dict[str, StrictBool] = Field(default={}, max_length=10)


class ApproveRequest(CamelModel):
    action_id: str = Field(pattern=r"^[0-9a-f]{16}$")  # the package's, as the person saw it
    task_title: str | None = Field(default=None, max_length=120)


class CriterionDto(CamelModel):
    criterion_id: str
    label: str
    fact: str
    status: CriterionStatus
    value: Any = None  # the fact compared: an age, "female", a card category, a percentage, true
    note: str | None = None  # why it is unknown or needs checking


class DocumentNeedDto(CamelModel):
    document_type: str
    document_id: str | None = None
    status: NeedStatus


class GapDto(CamelModel):
    """What to add or check: a paper (missing, unconfirmed, expired), a detail on one
    (detail_missing), or something to look at again (check)."""
    document_type: str | None = None
    document_id: str | None = None
    reason: Literal["missing", "unconfirmed", "expired", "detail_missing", "check"]
    message: str


class ToConfirmDto(CamelModel):
    key: str
    label: str
    confirmed: bool


class SchemeResultDto(CamelModel):
    scheme_id: str
    name: str
    summary: str | None = None
    benefit: str | None = None
    status: SchemeStatus
    exclusive_group: str | None = None
    criteria: list[CriterionDto]
    documents: list[DocumentNeedDto]
    gaps: list[GapDto] = []
    to_confirm: list[ToConfirmDto] = []


class PackageDocumentDto(CamelModel):
    document_type: str
    document_id: str | None = None


class TaskDto(CamelModel):
    title: str


class PackageDto(CamelModel):
    action_id: str
    scheme_id: str
    scheme_name: str
    documents: list[PackageDocumentDto]
    declarations: list[str] = []
    checklist: list[str]
    tasks: list[TaskDto]
    warning: str | None = None
    status: str  # proposed (waiting for approval) or approved_for_manual_handoff


class CheckDto(CamelModel):
    check_id: str
    mode: Literal["screen", "apply"]
    # screen: screened. apply: awaiting_review, ready_for_manual_handoff, needs_input,
    # not_eligible_in_demo, blocked_conflict; or blocked_source_review, needs_scheme_choice.
    status: str
    as_of: date
    scheme_id: str | None = None
    schemes: list[SchemeResultDto] = []
    notes: list[str] = []
    declarations: dict[str, bool] = {}
    package: PackageDto | None = None
    created_at: datetime | None = None


class CheckSummaryDto(CamelModel):
    check_id: str
    mode: str | None = None
    status: str
    scheme_id: str | None = None
    created_at: datetime


# ---- helpers ------------------------------------------------------------------------------

Benefits = Annotated[Any, Depends(get_benefits_service)]
Accounts = Annotated[Any, Depends(get_account_service)]


def envelope(data: Any, response: Response) -> ApiResponse[Any]:
    response.headers["Cache-Control"] = "no-store"  # personal
    return ApiResponse.of(data=data, version=get_config().app.version)


# ---- routes -------------------------------------------------------------------------------

@router.get("/schemes", response_model=ApiResponse[list[SchemeDto]], summary="The demo schemes and what they ask for")
def list_schemes(response: Response, actor: Actor, benefits: Benefits) -> ApiResponse[list[SchemeDto]]:
    return envelope([SchemeDto.model_validate(item) for item in benefits.schemes()], response)


@router.post("/checks", status_code=status.HTTP_201_CREATED, response_model=ApiResponse[CheckDto],
             summary="Check my papers: every scheme, or one scheme with my answers")
def create_check(body: CheckRequest, response: Response, actor: Actor, benefits: Benefits,
                 accounts: Accounts) -> ApiResponse[CheckDto]:
    name = accounts.saved_choices(actor)["display_name"]  # only if no paper names me
    result = benefits.check(actor, body.scheme_id, body.declarations, owner_name=name)
    return envelope(CheckDto.model_validate(result), response)


@router.post("/checks/{check_id}/approve", response_model=ApiResponse[CheckDto],
             summary="Approve the application package a check prepared")
def approve_check(check_id: str, body: ApproveRequest, response: Response, actor: Actor, benefits: Benefits,
                  accounts: Accounts) -> ApiResponse[CheckDto]:
    name = accounts.saved_choices(actor)["display_name"]
    result = benefits.approve(actor, check_id, body.action_id, task_title=body.task_title, owner_name=name)
    return envelope(CheckDto.model_validate(result), response)


@router.get("/checks/{check_id}", response_model=ApiResponse[CheckDto], summary="One of my scheme checks")
def get_check(check_id: str, response: Response, actor: Actor, benefits: Benefits) -> ApiResponse[CheckDto]:
    return envelope(CheckDto.model_validate(benefits.get(actor, check_id)), response)


@router.get("/checks", response_model=ApiResponse[list[CheckSummaryDto]],
            summary="My scheme checks from the last 30 days, newest first")
def list_checks(response: Response, actor: Actor, benefits: Benefits,
                limit: Annotated[int, Query(ge=1, le=50)] = 20) -> ApiResponse[list[CheckSummaryDto]]:
    return envelope([CheckSummaryDto.model_validate(item) for item in benefits.list(actor, limit)], response)
