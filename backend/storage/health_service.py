"""Doctor search requests (fastapi-backend Phase 2): runs graphs/health_graph.py for one person,
pauses when it asks a question, and keeps what the person was shown for RESULT_DAYS.

Same pattern as DocumentService: one graph_runs row per request, its id the thread id of the
encrypted checkpoint; an answer is applied once (the run is claimed first); the checkpoint is
deleted as soon as the run ends.

What is kept where:
- the encrypted checkpoint, only while a question waits (at most PAUSE_HOURS): the whole state,
  with the request text, the person's name and the phone's location;
- graph_runs.result, RESULT_DAYS from the request: what the person was shown (status, kind of
  doctor, the place's name, the doctors, the plain-text answer, the question asked). Never the
  request text, the person's name or the phone's coordinates;
- audit events: that a request was made, answered, and how it ended. Never the kind of doctor or
  the emergency signs (the audit trail is kept longer than results).

start() and answer() check and record the request at once, then return an iterator that runs
the graph: ("step", {"step": node}) for each step finished, last ("result", view).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator
from uuid import uuid4

from langgraph.types import Command
from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from ..errors import NotFoundException, ValidationException
from ..core.config import LimitsSection
from ..errors.exceptions import ConflictException, TooManyRequestsException
from ..graphs.health_graph import (
    GENERAL_PHYSICIAN_FIRST, LOCATION_NOT_FOUND, NO_DOCTORS, NO_TOP_CHOICE, NONE_VERIFIED, NOT_HANDLED, OLD_DETAILS,
    RELAXED_NOTES, SEARCH_UNAVAILABLE, build_health_graph,
)
from ..tools.doctor_rank import TOP
from ..tools.red_flags import find_red_flags
from ..tools.specialties import SPECIALTIES
from ..utils.checkpoints import forget_run
from .limits import count, wait_words
from .models import GraphRun
from .repository import audit
from .service import WorkflowFailed, run_config

logger = logging.getLogger(__name__)

GRAPH = "health"
RESULT_DAYS = 30  # what the person was shown is removed after this (user's choice)
PAUSE_HOURS = 24  # a question not answered in this time expires, and its checkpoint is deleted
STUCK_HOURS = 1  # a run still "running" after this was cut off (the server stopped): failed
WAITING = {"confirm_emergency": "needs_emergency_confirmation", "ask_clarification": "needs_clarification",
           "ask_location": "needs_location"}
ANSWER_FOR = {"needs_emergency_confirmation": "call_emergency", "needs_clarification": "clarification",
              "needs_location": "location"}
ANSWER_WORDS = {"call_emergency": "whether to call emergency services", "clarification": "an answer to the question",
                "location": "a place, or your location"}
NOT_UNDERSTOOD = "I could not understand what you need. Please start again and say it in your own words."
TOO_LONG = "The request became too long. Please start again with a shorter one."
EXPIRED = "This question was not answered in time. Please start again."
FAILED = "Something went wrong with this request. Please try again."
CLINIC_FIELDS = ("name", "locality", "address", "city", "hours", "lat", "lon")


def _pattern(template: str) -> re.Pattern[str]:
    """A fixed sentence with {placeholders}, as a pattern a filled-in copy matches."""
    return re.compile(".+".join(re.escape(part) for part in re.split(r"\{[a-z_]+\}", template)), re.DOTALL)


# The graph's warnings meant for the person. The others (a service down, the model not used) are
# technical and stay out of what is shown.
PERSON_NOTES = [_pattern(text) for text in (
    NO_DOCTORS, SEARCH_UNAVAILABLE, NONE_VERIFIED, NO_TOP_CHOICE, LOCATION_NOT_FOUND, GENERAL_PHYSICIAN_FIRST,
    OLD_DETAILS, *RELAXED_NOTES.values(), *(f"{label} can't be handled here yet" for label in NOT_HANDLED.values()))]


def now() -> datetime:
    return datetime.now(timezone.utc)


def person_notes(warnings: Any) -> list[str]:
    return [text for text in warnings or [] if isinstance(text, str) and any(p.fullmatch(text) for p in PERSON_NOTES)]


def doctor_view(doctor: dict[str, Any]) -> dict[str, Any]:
    clinic, reviews = doctor.get("clinic") or {}, doctor.get("reviews") or {}
    return {
        "doctor_id": doctor.get("doctor_id"), "name": doctor.get("name"), "gender": doctor.get("gender"),
        "specialty": doctor.get("specialty"), "qualifications": doctor.get("qualifications"),
        "experience_years": doctor.get("experience_years"), "languages": list(doctor.get("languages") or []),
        "fee": doctor.get("fee"), "clinic": {name: clinic.get(name) for name in CLINIC_FIELDS},
        "distance_km": doctor.get("distance_km"), "registration_level": doctor.get("registration_level"),
        "registration_note": doctor.get("registration_note"), "recommend_percent": reviews.get("recommend_percent"),
        "patient_stories": reviews.get("patient_stories"), "score": doctor.get("score"),
        "held_back": doctor.get("held_back"), "reasons": list(doctor.get("reasons") or []),
        "labels": list(doctor.get("labels") or []), "flags": list(doctor.get("flags") or []),
        "profile_url": doctor.get("profile_url"), "fetched_on": doctor.get("fetched_on"),
        "stale": bool(doctor.get("stale")),
    }


def empty_view(run_id: str, status: str, notes: list[str] | None = None) -> dict[str, Any]:
    return {"request_id": run_id, "status": status, "specialty": None, "doctor_fallback": None,
            "search_location": None, "search_radius_km": None, "doctors": [], "other_doctors": [],
            "is_top_choice": False, "answer": None, "notes": notes or [], "attribution": None,
            "question": None, "options": [], "emergency": None}


def emergency_view(details: dict[str, Any], source: str | None) -> dict[str, Any]:
    return {"numbers": list(details.get("numbers") or []), "message": details.get("message"),
            "red_flags": list(details.get("red_flags") or []), "model_signs": list(details.get("model_signs") or []),
            "source": source}


def result_view(run_id: str, values: dict[str, Any], waiting: str | None = None,
                payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """What the person is shown, from the graph's state (and, while a question waits, the
    question's payload). Only what is listed here is ever stored or sent."""
    status = WAITING[waiting] if waiting else values.get("status") or "failed"
    view = empty_view(run_id, status, person_notes(values.get("warnings")))
    specialty = values.get("specialty")
    if values.get("specialty_label") and specialty in SPECIALTIES:  # the kind of doctor is settled
        view["specialty"] = {"key": specialty, "label": SPECIALTIES[specialty]}
        view["doctor_fallback"] = values.get("doctor_fallback")
    place = values.get("search_location") or {}
    if place:
        view["search_location"] = {"label": place.get("label"), "source": place.get("source")}  # no coordinates
        view["attribution"] = place.get("attribution")
    if status == "doctors_found":
        ranked, top = values.get("doctors") or [], values.get("top_doctors") or []
        shown = top or ranked[:TOP]
        shown_ids = {doctor.get("doctor_id") for doctor in shown}
        view.update(search_radius_km=values.get("search_radius_km"), is_top_choice=bool(top),
                    answer=values.get("answer"), doctors=[doctor_view(doctor) for doctor in shown],
                    other_doctors=[doctor_view(doctor) for doctor in ranked if doctor.get("doctor_id") not in shown_ids])
    elif status == "no_doctors_nearby":
        view["search_radius_km"] = values.get("search_radius_km")
    elif status == "emergency":
        details = values.get("emergency") or {}
        view["emergency"] = emergency_view(details, details.get("source"))
    elif status == "not_understood":
        view["notes"].append(NOT_UNDERSTOOD)
    elif status == "rejected":
        view["notes"].append(TOO_LONG)
    if waiting:
        payload = payload or {}
        view["question"] = payload.get("question")
        view["options"] = [option for option in payload.get("options") or [] if isinstance(option, str)]
        if waiting == "confirm_emergency":
            view["emergency"] = emergency_view(payload, None)
    return view


def claim_graph_run(session: Session, run_id: str, from_status: str, **values: Any) -> bool:
    """Atomically move a run on from from_status (default: to running). False if another request
    got there first, so one answer can't be applied twice."""
    result = session.execute(update(GraphRun).where(GraphRun.id == run_id, GraphRun.status == from_status)
                             .values(**({"status": "running"} | values)))
    return result.rowcount == 1


class HealthService:
    def __init__(self, sessions: sessionmaker, checkpointer: Any, limits: LimitsSection | None = None):
        self.sessions = sessions
        self.checkpointer = checkpointer
        self.limits = limits or LimitsSection()
        self.graph = build_health_graph(checkpointer=checkpointer)  # refuses an unencrypted one

    # ---- asking ------------------------------------------------------------------------------

    def start(self, owner_id: str, query: str, *, current_location: dict[str, float] | None = None,
              home_location: dict[str, float] | None = None, use_llm: bool = False,
              medical_cloud_consent: bool = False, owner_name: str | None = None) -> Iterator[tuple[str, dict[str, Any]]]:
        """owner_name is hidden from the model's prompt; current_location is the phone's (lat, lon);
        home_location the home saved in the profile, used only when the person means home."""
        self._count_search(owner_id, emergency=bool(find_red_flags(query)))
        self.purge_expired()
        run_id = str(uuid4())
        with self.sessions.begin() as session:
            session.add(GraphRun(id=run_id, owner_id=owner_id, graph=GRAPH, status="running", result={}))
            audit(session, owner_id, "health.request_started", None, run_id=run_id, use_llm=use_llm,
                  medical_cloud=medical_cloud_consent, location_shared=current_location is not None,
                  home_saved=home_location is not None)
        state = {"actor_id": owner_id, "query": query, "use_interrupt": True, "use_llm": use_llm,
                 "medical_cloud_consent": medical_cloud_consent,
                 **({"current_location": current_location} if current_location else {}),
                 **({"home_location": home_location} if home_location else {}),
                 **({"owner_name": owner_name} if owner_name else {})}
        return self._steps(owner_id, run_id, state)

    def answer(self, owner_id: str, run_id: str, kind: str, value: Any) -> Iterator[tuple[str, dict[str, Any]]]:
        """kind: call_emergency (value True or False), clarification (text) or location (text, or
        {"lat", "lon"}); it must be the one the waiting question asks for."""
        self._count_search(owner_id, emergency=kind == "call_emergency")
        self.purge_expired()
        with self.sessions.begin() as session:
            run = self._owned(session, owner_id, run_id)
            if run.status == "expired":
                raise ConflictException(EXPIRED)
            if run.status != "paused":
                raise ConflictException("This request is not waiting for an answer")
            wanted = ANSWER_FOR[run.outcome]
            if kind != wanted:
                raise ValidationException(f"This request is waiting for {ANSWER_WORDS[wanted]}",
                                          details={"waitingFor": wanted})
            if not claim_graph_run(session, run_id, "paused"):
                raise ConflictException("This answer is already being handled")
            audit(session, owner_id, "health.answered", None, run_id=run_id, kind=kind)
        resume = {"call_emergency": value} if kind == "call_emergency" else value
        return self._steps(owner_id, run_id, Command(resume=resume))

    def _count_search(self, owner_id: str, emergency: bool) -> None:
        """limits.doctor_searches_per_hour per person (requests and answers: each may use the AI
        model, place lookups and a doctor-list refresh). An emergency is never refused: a request
        the fixed rules see as one (no model, no network) and the answer to "call emergency?"
        always go through, and the refusal itself says to call 112."""
        with self.sessions.begin() as session:
            wait = count(session, f"doctor-search:{owner_id}", self.limits.doctor_searches_per_hour,
                         timedelta(hours=1))
        if wait is not None and not emergency:
            raise TooManyRequestsException(
                f"You have asked many times in the last hour. Please wait {wait_words(wait)} and try again. "
                "In an emergency, call 112 or 108 now.", wait)

    def _steps(self, owner_id: str, run_id: str, graph_input: Any) -> Iterator[tuple[str, dict[str, Any]]]:
        config = run_config(run_id)
        try:
            for update_ in self.graph.stream(graph_input, config, stream_mode="updates"):
                for step in update_:
                    if not step.startswith("__"):  # "__interrupt__" is not a step
                        yield "step", {"step": step}
            snapshot = self.graph.get_state(config)
        except Exception as exc:
            self._record_failure(owner_id, run_id, exc)
            raise WorkflowFailed("The request could not be handled; please try again") from None
        yield "result", self._save_outcome(owner_id, run_id, snapshot)

    def _save_outcome(self, owner_id: str, run_id: str, snapshot: Any) -> dict[str, Any]:
        waiting = next((task for task in snapshot.tasks if task.interrupts), None)
        view = result_view(run_id, snapshot.values, waiting.name if waiting else None,
                           waiting.interrupts[0].value if waiting else None)
        with self.sessions.begin() as session:
            run = session.get(GraphRun, run_id)
            run.status, run.outcome, run.result = ("paused" if waiting else "ended"), view["status"], view
            if not waiting:
                run.ended_at = now()
            audit(session, owner_id, f"health.{view['status']}", None, run_id=run_id)
        if not waiting:
            forget_run(self.checkpointer, run_id)
        return view

    def _record_failure(self, owner_id: str, run_id: str, exc: Exception) -> None:
        logger.error("A health request failed: %s", type(exc).__name__)  # never the message: it may hold the request
        with self.sessions.begin() as session:
            run = session.get(GraphRun, run_id)
            run.status, run.outcome, run.result, run.ended_at = "failed", "failed", empty_view(run_id, "failed",
                                                                                              [FAILED]), now()
            audit(session, owner_id, "health.failed", None, run_id=run_id, error=type(exc).__name__)
        self._forget([run_id])

    def _forget(self, run_ids: list[str]) -> None:
        for run_id in run_ids:
            try:
                forget_run(self.checkpointer, run_id)
            except Exception as exc:  # encrypted, so harmless, but logged
                logger.error("A health request's checkpoint could not be removed: %s", type(exc).__name__)

    # ---- reading -----------------------------------------------------------------------------

    def _owned(self, session: Session, owner_id: str, run_id: str) -> GraphRun:
        run = session.execute(select(GraphRun).where(
            GraphRun.id == run_id, GraphRun.owner_id == owner_id, GraphRun.graph == GRAPH,
            GraphRun.created_at >= now() - timedelta(days=RESULT_DAYS))).scalar_one_or_none()
        if run is None:  # also someone else's
            raise NotFoundException("This health request was not found")
        return run

    def get(self, owner_id: str, run_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            run = self._owned(session, owner_id, run_id)
            return run.result or empty_view(run_id, run.status)

    def list(self, owner_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """The person's requests from the last RESULT_DAYS, newest first."""
        with self.sessions() as session:
            runs = session.execute(
                select(GraphRun).where(GraphRun.owner_id == owner_id, GraphRun.graph == GRAPH,
                                       GraphRun.created_at >= now() - timedelta(days=RESULT_DAYS))
                .order_by(GraphRun.created_at.desc(), GraphRun.id).limit(limit)).scalars().all()
        return [{"request_id": run.id, "status": run.outcome or run.status,
                 "specialty": (run.result or {}).get("specialty"), "created_at": run.created_at} for run in runs]

    # ---- keeping only what is needed ---------------------------------------------------------

    def purge_expired(self) -> None:
        """A question not answered within PAUSE_HOURS expires, and a run cut off mid-way (still
        running after STUCK_HOURS) fails: their checkpoints, which hold the request, are deleted.
        Runs older than RESULT_DAYS are removed."""
        current = now()
        forget = []
        with self.sessions.begin() as session:
            stale = session.execute(select(GraphRun.id, GraphRun.status).where(GraphRun.graph == GRAPH, or_(
                and_(GraphRun.status == "paused", GraphRun.updated_at < current - timedelta(hours=PAUSE_HOURS)),
                and_(GraphRun.status == "running", GraphRun.updated_at < current - timedelta(hours=STUCK_HOURS))))).all()
            for run_id, status in stale:
                ended = "expired" if status == "paused" else "failed"
                view = empty_view(run_id, ended, [EXPIRED if ended == "expired" else FAILED])
                if claim_graph_run(session, run_id, status, status=ended, outcome=ended, result=view, ended_at=current):
                    forget.append(run_id)
            session.execute(delete(GraphRun).where(
                GraphRun.graph == GRAPH, GraphRun.created_at < current - timedelta(days=RESULT_DAYS),
                GraphRun.status.notin_(("paused", "running"))))
        self._forget(forget)
