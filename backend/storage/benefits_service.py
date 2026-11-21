"""Benefit scheme checks (benefits Part 2): runs graphs/benefits_graph.py on the person's own
vault, keeps what they were shown for RESULT_DAYS, and approves a handoff package.

- check(): with no scheme, screens every listed demo scheme ("Schemes for me"); with one, checks
  it and, when it passes, drafts the package that waits for the person's approval.
- approve(): the person approves that package. The graph is run again on the vault as it is now:
  if a paper, a declaration or the date changed since, the package's action_id changes and the
  approval is refused (409), so an approval never covers papers the person did not see.

The graph runs to the end in one call (no pause, so no checkpoint). One graph_runs row per check
(graph "benefits"); its result holds only what the person is shown: scheme names, each rule's
outcome and the fact it compared (an age, "female", a card category, a percentage), what to add,
and the package's papers and checklist. Never a name or an ID number: the graph's messages quote
none, and the vault's fields are already masked. Audit events say a check was made and how it
ended, never the facts.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, sessionmaker

from ..errors import NotFoundException, ValidationException
from ..errors.exceptions import ConflictException
from ..graphs.benefits_graph import build_benefits_graph
from ..tools.schemes import NATIONAL, SCHEMES, listed_schemes
from .models import GraphRun
from .repository import audit, owned_documents
from .service import WorkflowFailed, india_today, vault_record

logger = logging.getLogger(__name__)

GRAPH = "benefits"
RESULT_DAYS = 30  # like doctor search: what the person was shown is removed after this
WAITING = "awaiting_review"
DONE = "ready_for_manual_handoff"
CHANGED = "Your papers or answers have changed since this check, or a day has passed. Please check again."
PERSON_NOTES = {
    "blocked_source_review": "The demo scheme list could not be checked right now, so no answer was given.",
    "needs_scheme_choice": "Please choose a scheme from the list.",
    "blocked_quality_gate": "The application could not be prepared. Please check again.",
    "failed_tool": "Something went wrong. Please try again.",
}


def now() -> datetime:
    return datetime.now(timezone.utc)


def catalogue_view(scheme: dict[str, Any]) -> dict[str, Any]:
    return {
        "scheme_id": scheme["scheme_id"], "name": scheme["name"], "summary": scheme.get("summary"),
        "benefit": scheme.get("benefit"), "exclusive_group": scheme.get("exclusive_group"),
        "required_documents": list(scheme["required_documents"]),
        "criteria": [{"criterion_id": rule["id"], "label": rule.get("label") or rule["id"]}
                     for rule in scheme["criteria"]],
        "declarations": [{"key": item["key"], "label": item["label"]} for item in scheme.get("declarations", [])],
        "apply_at": scheme.get("apply_at"),
    }


def scheme_view(assessment: dict[str, Any]) -> dict[str, Any]:
    return {
        "scheme_id": assessment["scheme_id"], "name": assessment["name"], "summary": assessment.get("summary"),
        "benefit": assessment.get("benefit"), "status": assessment["status"],
        "exclusive_group": assessment.get("exclusive_group"),
        "criteria": [{"criterion_id": item["criterion_id"], "label": item["label"], "fact": item["fact"],
                      "status": item["status"], "value": item.get("actual"), "note": item.get("note")}
                     for item in assessment["criteria"]],
        "documents": [{"document_type": item["required_type"], "document_id": item["document_id"],
                       "status": item["status"]} for item in assessment["documents"]],
        "gaps": [dict(gap) for gap in assessment["gaps"]],
        "to_confirm": [dict(item) for item in assessment["to_confirm"]],
    }


def package_view(values: dict[str, Any]) -> dict[str, Any] | None:
    package = values.get("application_package") or values.get("package_proposal")
    if not package or values.get("status") not in (WAITING, DONE):
        return None
    return {
        "action_id": package["action_id"], "scheme_id": package["scheme_id"], "scheme_name": package["scheme_name"],
        "documents": [dict(item) for item in package.get("documents", [])],
        "declarations": list(package.get("declarations", [])), "checklist": list(package.get("checklist", [])),
        "tasks": [{"title": task["title"]} for task in package.get("tasks", [])],
        "warning": package.get("warning"),
        "status": package.get("status", "proposed"),
    }


def result_view(check_id: str, mode: str, values: dict[str, Any], as_of: date,
                declarations: dict[str, bool]) -> dict[str, Any]:
    """What the person is shown, from the graph's final state. Only what is listed here is stored."""
    status = values.get("status") or "failed_tool"
    notes = [*values.get("notes", []), *([PERSON_NOTES[status]] if status in PERSON_NOTES else [])]
    return {
        "check_id": check_id, "mode": mode, "status": status, "as_of": as_of.isoformat(),
        "scheme_id": values.get("scheme_id") if mode == "apply" else None,
        "schemes": [scheme_view(item) for item in values.get("scheme_assessments", [])],
        "notes": notes, "declarations": dict(declarations), "package": package_view(values),
    }


class BenefitsService:
    def __init__(self, sessions: sessionmaker):
        self.sessions = sessions
        self.graph = build_benefits_graph()

    # ---- the catalogue -----------------------------------------------------------------------

    def schemes(self) -> list[dict[str, Any]]:
        """The demo schemes "Schemes for me" checks (national, so the same in every state)."""
        return [catalogue_view(scheme) for scheme in listed_schemes(NATIONAL)]

    # ---- checking ----------------------------------------------------------------------------

    def check(self, owner_id: str, scheme_id: str | None = None, declarations: dict[str, bool] | None = None, *,
              owner_name: str | None = None, as_of: date | None = None) -> dict[str, Any]:
        """Screen every listed scheme (scheme_id None), or check one and draft its package.
        declarations: the person's own answers ({"not_remarried": true}); only the scheme's own are kept."""
        declarations = dict(declarations or {})
        if scheme_id is not None:
            scheme = SCHEMES.get(scheme_id)
            if scheme is None or not scheme.get("listed"):
                raise NotFoundException("This scheme is not in the demo list")
            keys = {item["key"] for item in scheme.get("declarations", [])}
            unknown = sorted(set(declarations) - keys)
            if unknown:
                raise ValidationException("This scheme does not ask about: " + ", ".join(unknown))
        elif declarations:
            raise ValidationException("Answers are given when checking one scheme")
        self.purge_expired()
        day = as_of or india_today()
        mode = "screen" if scheme_id is None else "apply"
        check_id = str(uuid4())
        values = self._run(owner_id, check_id, mode, scheme_id, declarations, owner_name, day)
        view = result_view(check_id, mode, values, day, declarations)
        with self.sessions.begin() as session:
            session.add(GraphRun(id=check_id, owner_id=owner_id, graph=GRAPH, status="ended", outcome=view["status"],
                                 result=view, ended_at=now()))
            audit(session, owner_id, "benefits.checked", None, check_id=check_id, mode=mode, scheme_id=scheme_id,
                  status=view["status"])
        return self.get(owner_id, check_id)

    def approve(self, owner_id: str, check_id: str, action_id: str, *, task_title: str | None = None,
                owner_name: str | None = None) -> dict[str, Any]:
        """The person approves the package a check drafted (its action_id, as they saw it), with
        the task's title changed if they like. The package is then ready to take to the office."""
        with self.sessions() as session:
            run = self._owned(session, owner_id, check_id)
            saved, outcome = dict(run.result or {}), run.outcome
        if saved.get("mode") != "apply" or outcome != WAITING or not saved.get("package"):
            raise ConflictException("This check is not waiting for your approval")
        if saved["package"]["action_id"] != action_id:
            raise ConflictException(CHANGED)
        declarations = dict(saved.get("declarations") or {})
        day = india_today()
        again = self._run(owner_id, check_id, "apply", saved["scheme_id"], declarations, owner_name, day)
        if again.get("status") != WAITING or again["package_proposal"]["action_id"] != action_id:
            raise ConflictException(CHANGED)
        title = " ".join((task_title or "").split())
        decision = {"action": "edit" if title else "approve", "reviewer_id": owner_id,
                    **({"edits": {"task_title": title}} if title else {})}
        values = self._run(owner_id, check_id, "apply", saved["scheme_id"], declarations, owner_name, day,
                           review_decision=decision)
        if values.get("status") != DONE:
            raise ConflictException(CHANGED)
        view = result_view(check_id, "apply", values, day, declarations)
        with self.sessions.begin() as session:
            claimed = session.execute(update(GraphRun).where(
                GraphRun.id == check_id, GraphRun.owner_id == owner_id, GraphRun.graph == GRAPH,
                GraphRun.outcome == WAITING).values(outcome=view["status"], result=view, ended_at=now()))
            if claimed.rowcount != 1:
                raise ConflictException("This check was already approved")
            audit(session, owner_id, "benefits.approved", None, check_id=check_id, scheme_id=saved["scheme_id"])
        return self.get(owner_id, check_id)

    def _run(self, owner_id: str, check_id: str, mode: str, scheme_id: str | None, declarations: dict[str, bool],
             owner_name: str | None, day: date, review_decision: dict[str, Any] | None = None) -> dict[str, Any]:
        with self.sessions() as session:
            documents = [record for document in owned_documents(session, owner_id)
                         if (record := vault_record(document)) is not None]
        state: dict[str, Any] = {
            "actor_id": owner_id, "case_id": check_id, "case_version": 1, "expected_case_version": 1,
            "mode": mode, "jurisdiction": NATIONAL, "as_of": day.isoformat(), "documents": documents,
            "declarations": declarations, "profile_facts": {},
            **({"scheme_id": scheme_id} if scheme_id else {}),
            **({"owner_name": owner_name} if owner_name else {}),
            **({"review_decision": review_decision} if review_decision else {}),
        }
        try:
            return self.graph.invoke(state)
        except Exception as exc:
            logger.error("A benefits check failed: %s", type(exc).__name__)  # never the message
            raise WorkflowFailed("The schemes could not be checked; please try again") from None

    # ---- reading -----------------------------------------------------------------------------

    def _owned(self, session: Session, owner_id: str, check_id: str) -> GraphRun:
        run = session.execute(select(GraphRun).where(
            GraphRun.id == check_id, GraphRun.owner_id == owner_id, GraphRun.graph == GRAPH,
            GraphRun.created_at >= now() - timedelta(days=RESULT_DAYS))).scalar_one_or_none()
        if run is None:  # also someone else's
            raise NotFoundException("This scheme check was not found")
        return run

    def get(self, owner_id: str, check_id: str) -> dict[str, Any]:
        with self.sessions() as session:
            run = self._owned(session, owner_id, check_id)
            return {**(run.result or {}), "created_at": run.created_at}

    def list(self, owner_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """The person's checks from the last RESULT_DAYS, newest first."""
        with self.sessions() as session:
            runs = session.execute(
                select(GraphRun).where(GraphRun.owner_id == owner_id, GraphRun.graph == GRAPH,
                                       GraphRun.created_at >= now() - timedelta(days=RESULT_DAYS))
                .order_by(GraphRun.created_at.desc(), GraphRun.id).limit(limit)).scalars().all()
        return [{"check_id": run.id, "mode": (run.result or {}).get("mode"), "status": run.outcome or run.status,
                 "scheme_id": (run.result or {}).get("scheme_id"), "created_at": run.created_at} for run in runs]

    def purge_expired(self) -> None:
        """Checks older than RESULT_DAYS are removed."""
        with self.sessions.begin() as session:
            session.execute(delete(GraphRun).where(GraphRun.graph == GRAPH,
                                                   GraphRun.created_at < now() - timedelta(days=RESULT_DAYS)))
