"""Benefits graph: which made-up demo schemes a senior may get from their confirmed papers, and a
reviewed handoff package for one of them (benefits Part 2; schemes in tools/schemes.py).

Two modes:
- screen: every listed scheme for the person's state. Ends with one assessment per scheme:
  likely, add_documents, not_eligible or please_check, each saying what to add or check.
  Nothing is drafted or approved.
- apply (the default): one scheme (scheme_id, or picked from need_text). If it passes, a handoff
  package is drafted (the papers to take, a checklist, a task), quality-gated and approved by the
  owner (a prefilled review_decision, or a LangGraph interrupt with a checkpointer).

Every fact cites the confirmed document field it came from. Two papers are linked where one fact
needs both: a death certificate that names the owner as the husband or wife makes "widowed"
true; a ration card that names the owner gives the household's card category. Names are never
corrected: a near match ("Kamala" for "Kamla") is sent to the person to check. What no paper
shows ("I have not married again") is declared by the person, so the result is only "likely".

This graph does not upload documents or submit an application; it touches no database.

load_case_context -> resolve_schemes -> retrieve_scheme_evidence -> validate_rule_set
  -> collect_confirmed_facts -> link_documents -> evaluate_criteria -> match_documents (subgraph)
  -> reconcile_assessment -> screen: END
                           -> apply: draft_handoff -> quality_gate -> human_review
                                     -> revalidate_review -> commit_handoff -> finalize_case
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import date
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from ..tools.demo_tools import evaluate_rule
from ..tools.document_rules import match_requirements
from ..tools.schemes import (
    DIRECT_FACTS,
    DOCUMENT_WORDS,
    EXCLUSIVE_GROUPS,
    FACT_DOCUMENT,
    NATIONAL,
    OPERATORS,
    SCHEMES,
    compare_names,
    covers,
    get_scheme,
    listed_schemes,
    names_listed,
)

MODES = ("apply", "screen")
OWNER_NAMES_DIFFER = "Confirmed documents have different owner names"


class BenefitsState(TypedDict, total=False):
    # Handwritten inputs
    actor_id: str
    case_id: str
    case_version: int
    expected_case_version: int
    mode: str  # apply (default) or screen
    scheme_id: str
    scheme_ids: list[str]  # screen: only these (default: every listed scheme)
    need_text: str
    jurisdiction: str  # a state code; screen defaults to national schemes only
    as_of: str
    owner_name: str  # the account's name, used only when no confirmed paper names the owner
    profile_facts: dict[str, dict[str, Any]]
    documents: list[dict[str, Any]]
    declarations: dict[str, bool]  # what the person says themselves (scheme["declarations"])
    review_decision: dict[str, Any]
    use_interrupt: bool
    use_llm: bool
    simulate_source_unavailable: bool

    # Graph outputs / control
    schemes: list[dict[str, Any]]
    scheme: dict[str, Any]
    source_snapshot: dict[str, Any]
    source_snapshots: dict[str, dict[str, Any]]
    papers_name: str | None  # the owner's name as the confirmed papers give it
    facts: dict[str, dict[str, Any]]
    fact_notes: dict[str, dict[str, Any]]  # why a linked fact is unknown or needs checking
    conflicting_facts: list[str]
    contradictions: list[str]
    criterion_results: list[dict[str, Any]]
    criterion_results_by_scheme: dict[str, list[dict[str, Any]]]
    document_matches: list[dict[str, Any]]
    document_matches_by_scheme: dict[str, list[dict[str, Any]]]
    missing_inputs: list[str]
    assessment: dict[str, Any]
    scheme_assessments: list[dict[str, Any]]
    notes: list[str]
    package_proposal: dict[str, Any]
    application_package: dict[str, Any]
    created_tasks: list[dict[str, Any]]
    committed_action_id: str
    events: list[str]
    errors: list[str]
    warnings: list[str]
    status: str
    route: str


def add_event(state: BenefitsState, name: str) -> list[str]:
    return [*state.get("events", []), name]


def load_case_context(state: BenefitsState) -> dict[str, Any]:
    mode = state.get("mode") or "apply"
    errors = []
    if mode not in MODES:
        errors.append("mode must be apply or screen")
    required = ("actor_id", "case_id", "jurisdiction") if mode == "apply" else ("actor_id", "case_id")
    for key in required:
        if not state.get(key):
            errors.append(f"{key} is required")
    if state.get("case_version") != state.get("expected_case_version"):
        errors.append("Case version changed; start a new assessment run")
    try:
        date.fromisoformat(state.get("as_of", ""))
    except ValueError:
        errors.append("as_of must be an ISO date (YYYY-MM-DD)")
    declarations = state.get("declarations") or {}
    if not isinstance(declarations, dict) or not all(isinstance(value, bool) for value in declarations.values()):
        errors.append("declarations must map each statement to true or false")
    update: dict[str, Any] = {
        "mode": mode,
        "errors": errors,
        "status": "blocked" if errors else "evaluating",
        "route": "stop" if errors else "continue",
        "events": add_event(state, "load_case_context"),
    }
    if mode == "screen" and not state.get("jurisdiction"):
        update["jurisdiction"] = NATIONAL
    return update


def resolve_scheme(state: BenefitsState) -> dict[str, Any]:
    """screen: every listed scheme for the place. apply: one scheme from a curated local list."""
    jurisdiction = state["jurisdiction"]
    if state["mode"] == "screen":
        wanted = set(state.get("scheme_ids") or [])
        schemes = [scheme for scheme in listed_schemes(jurisdiction) if not wanted or scheme["scheme_id"] in wanted]
        if not schemes:
            return {"status": "needs_scheme_choice", "route": "stop",
                    "warnings": ["None of the chosen schemes is in the demo catalogue for this place"],
                    "events": add_event(state, "resolve_scheme:none_listed")}
        return {"schemes": schemes, "route": "continue",
                "events": add_event(state, f"resolve_scheme:screen_{len(schemes)}")}
    scheme_id = state.get("scheme_id")
    if not scheme_id and state.get("use_llm"):
        try:
            from ..agents.llm_agents import choose_scheme

            candidates = [scheme for scheme in SCHEMES.values() if covers(scheme, jurisdiction)]
            scheme_id = choose_scheme(state.get("need_text", ""), candidates).scheme_id
        except Exception as exc:
            return {
                "status": "failed_tool",
                "route": "stop",
                "errors": [f"Scheme selection LLM unavailable: {exc}"],
                "events": add_event(state, "resolve_scheme:llm_error"),
            }
    if not scheme_id and not state.get("use_llm") and "pension" in state.get("need_text", "").lower():
        scheme_id = "demo_senior_support"
    scheme = get_scheme(scheme_id or "")
    if not scheme:
        return {
            "status": "needs_scheme_choice",
            "route": "stop",
            "warnings": ["Choose a scheme from the curated demo catalogue"],
            "events": add_event(state, "resolve_scheme:unknown"),
        }
    if not covers(scheme, jurisdiction):
        return {
            "status": "needs_scheme_choice",
            "route": "stop",
            "warnings": ["The selected scheme does not cover this jurisdiction"],
            "events": add_event(state, "resolve_scheme:wrong_jurisdiction"),
        }
    return {
        "scheme": scheme,
        "scheme_id": scheme_id,
        "schemes": [scheme],
        "route": "continue",
        "events": add_event(state, f"resolve_scheme:{scheme_id}"),
    }


def retrieve_scheme_evidence(state: BenefitsState) -> dict[str, Any]:
    if state.get("simulate_source_unavailable"):
        return {
            "status": "blocked_source_review",
            "route": "stop",
            "errors": ["Curated scheme source is unavailable; no eligibility decision was made"],
            "events": add_event(state, "retrieve_scheme_evidence:unavailable"),
        }
    snapshots = {scheme["scheme_id"]: dict(scheme["source"]) for scheme in state["schemes"]}
    update = {"source_snapshots": snapshots, "route": "continue",
              "events": add_event(state, "retrieve_scheme_evidence:success")}
    if state["mode"] == "apply":
        update["source_snapshot"] = snapshots[state["scheme_id"]]
    return update


def rule_set_valid(scheme: dict[str, Any], source: dict[str, Any], as_of: str) -> bool:
    """Approved for the demo, at most a year old, and every criterion and declaration well formed."""
    criteria = scheme.get("criteria", [])
    try:
        source_age_days = (date.fromisoformat(as_of) - date.fromisoformat(source["retrieved_at"])).days
    except (KeyError, ValueError):
        source_age_days = 9999
    return (
        source.get("approved_for_demo") is True
        and bool(source.get("source_id"))
        and 0 <= source_age_days <= 365
        and bool(criteria)
        and all(c.get("id") and c.get("fact") and c.get("operator") in OPERATORS
                and (c["operator"] != "in" or isinstance(c.get("value"), list)) for c in criteria)
        and all(item.get("key") and item.get("label") for item in scheme.get("declarations", []))
    )


def validate_rule_set(state: BenefitsState) -> dict[str, Any]:
    snapshots = state["source_snapshots"]
    valid = [scheme for scheme in state["schemes"]
             if rule_set_valid(scheme, snapshots[scheme["scheme_id"]], state["as_of"])]
    if not valid or (state["mode"] == "apply" and len(valid) != len(state["schemes"])):
        return {
            "status": "blocked_source_review",
            "route": "stop",
            "errors": ["The demo rule set lacks validated source or criterion fields"],
            "events": add_event(state, "validate_rule_set:failed"),
        }
    update: dict[str, Any] = {"route": "continue", "events": add_event(state, "validate_rule_set:passed")}
    left_out = [scheme["name"] for scheme in state["schemes"] if scheme not in valid]
    if left_out:  # screen only: the others are still checked
        update["schemes"] = valid
        update["warnings"] = [*state.get("warnings", []),
                              *(f"{name} was left out: its rules could not be checked" for name in left_out)]
    return update


def usable_documents(state: BenefitsState) -> list[dict[str, Any]]:
    """The owner's confirmed documents that have not expired on as_of."""
    as_of = date.fromisoformat(state["as_of"])
    usable = []
    for document in state.get("documents", []):
        if document.get("status") != "confirmed" or document.get("owner_id") != state["actor_id"]:
            continue
        expiry = document.get("expiry_date")
        if expiry:
            try:
                if date.fromisoformat(expiry) < as_of:
                    continue
            except ValueError:
                continue
        usable.append(document)
    return usable


def collect_confirmed_facts(state: BenefitsState) -> dict[str, Any]:
    facts: dict[str, dict[str, Any]] = {}
    contradictions = []
    conflicting = set()
    document_names: dict[str, str] = {}  # compared form -> as printed
    for key, fact in state.get("profile_facts", {}).items():
        if fact.get("confirmed") and fact.get("source_ref"):
            facts[key] = {"value": fact.get("value"), "source_ref": fact["source_ref"]}
    for document in usable_documents(state):
        fields = document.get("confirmed_fields", {})
        name = fields.get("name", {}).get("value")
        if name:
            document_names.setdefault(str(name).strip().casefold(), str(name).strip())
        for field_name in DIRECT_FACTS:
            if field_name not in fields:
                continue
            field = fields[field_name]
            new_fact = {"value": field.get("value"), "source_ref": field.get("source_ref")}
            if not new_fact["source_ref"]:
                continue
            if field_name in facts and facts[field_name]["value"] != new_fact["value"]:
                contradictions.append(f"Conflicting confirmed values for {field_name}")
                conflicting.add(field_name)
            else:
                facts.setdefault(field_name, new_fact)
    if len(document_names) > 1:
        contradictions.append(OWNER_NAMES_DIFFER)
    if "date_of_birth" in conflicting:
        conflicting.add("age")
    if "date_of_birth" in facts:
        try:
            dob = date.fromisoformat(str(facts["date_of_birth"]["value"]))
            as_of = date.fromisoformat(state["as_of"])
            age = as_of.year - dob.year - ((as_of.month, as_of.day) < (dob.month, dob.day))
            age_fact = {"value": age, "source_ref": facts["date_of_birth"]["source_ref"]}
            if "age" in facts and facts["age"]["value"] != age:
                contradictions.append("Profile age conflicts with confirmed date of birth")
                conflicting.add("age")
            else:
                facts["age"] = age_fact
        except ValueError:
            contradictions.append("Confirmed date of birth is invalid")
            conflicting.add("age")
    papers_name = next(iter(document_names.values())) if len(document_names) == 1 else None
    return {
        "facts": facts,
        "contradictions": list(dict.fromkeys(contradictions)),
        "conflicting_facts": sorted(conflicting),
        "papers_name": papers_name if papers_name else (None if document_names else state.get("owner_name")),
        "events": add_event(state, "collect_confirmed_facts"),
    }


# How well a linked paper fits the owner, best first.
LINK_RANK = ("same", "close", "no_spouse_name", "no_members", "different", "after_today", "no_owner")
CARD_NOTES = {
    "close": ("conflict", "Your name on the ration card is spelt a little differently from your ID card. "
                          "Please check it."),
    "no_members": ("unknown", "The ration card's head of family is someone else. Add the family members' names "
                              "from the card, so we can see you are on it."),
    "different": ("conflict", "You are not named on this ration card. Please check the card, or add one that "
                              "names you."),
    "no_owner": ("unknown", "Add your ID card first, so we can match your name with the ration card."),
}
DEATH_NOTES = {
    "close": ("conflict", "The husband's or wife's name on the death certificate is spelt a little differently "
                          "from your name. Please check it."),
    "no_spouse_name": ("unknown", "The death certificate does not show the husband's or wife's name. Add it as "
                                  "printed on the certificate."),
    "different": ("conflict", "The death certificate names someone else as the husband or wife. Please check it "
                              "is the right certificate."),
    "after_today": ("conflict", "The date of death on the certificate is after today. Please check it."),
    "no_owner": ("unknown", "Add your ID card first, so we can match your name with the death certificate."),
}


def on_ration_card(owner: str | None, fields: dict[str, Any]) -> str:
    if not owner:
        return "no_owner"
    head = fields.get("head_of_family", {}).get("value")
    members = names_listed(fields.get("family_members", {}).get("value"))
    outcomes = [compare_names(owner, name) for name in [head, *members] if name]
    if "same" in outcomes:
        return "same"
    if "close" in outcomes:
        return "close"
    return "different" if members else "no_members"


def named_as_spouse(owner: str | None, fields: dict[str, Any], as_of: str) -> str:
    died = fields.get("date_of_death", {}).get("value")
    try:
        if died and date.fromisoformat(str(died)) > date.fromisoformat(as_of):
            return "after_today"
    except ValueError:
        return "after_today"
    if not owner:
        return "no_owner"
    spouse = fields.get("spouse_name", {}).get("value")
    if not spouse:
        return "no_spouse_name"
    return compare_names(owner, spouse)


def link_documents(state: BenefitsState) -> dict[str, Any]:
    """Facts that need two papers: the owner's name (from the ID) found on a ration card or as
    the spouse on a death certificate. The best-fitting paper of each kind is used."""
    facts = dict(state["facts"])
    notes: dict[str, dict[str, Any]] = {}
    owner = state.get("papers_name")
    documents = usable_documents(state)
    links = (
        ("ration_card", "ration_card_category", "card_category", CARD_NOTES,
         lambda fields: on_ration_card(owner, fields)),
        ("death_certificate", "widowed", "spouse_name", DEATH_NOTES,
         lambda fields: named_as_spouse(owner, fields, state["as_of"])),
    )
    events = []
    for document_type, fact, proving_field, words, outcome_of in links:
        papers = [document for document in documents if document.get("type") == document_type]
        if not papers or fact in state.get("conflicting_facts", []):
            continue
        outcome, paper = min(((outcome_of(paper.get("confirmed_fields", {})), paper) for paper in papers),
                             key=lambda pair: LINK_RANK.index(pair[0]))
        events.append(f"{document_type}:{outcome}")
        field = paper.get("confirmed_fields", {}).get(proving_field) or {}
        if outcome == "same" and field.get("value") is not None and field.get("source_ref"):
            facts[fact] = {"value": True if fact == "widowed" else field["value"], "source_ref": field["source_ref"]}
        else:
            status, message = words.get(outcome, ("unknown", "Please check this paper's details."))
            notes[fact] = {"status": status, "message": message, "document_type": document_type,
                           "document_id": paper.get("document_id"), "reason": outcome}
    return {"facts": facts, "fact_notes": notes,
            "events": add_event(state, "link_documents" + (f":{','.join(events)}" if events else ""))}


def evaluate_criteria(state: BenefitsState) -> dict[str, Any]:
    by_scheme = {}
    conflicting = set(state.get("conflicting_facts", []))
    notes = state.get("fact_notes", {})
    for scheme in state["schemes"]:
        source_id = state["source_snapshots"][scheme["scheme_id"]]["source_id"]
        results = []
        for rule in scheme["criteria"]:
            fact = state["facts"].get(rule["fact"])
            note = notes.get(rule["fact"])
            if rule["fact"] in conflicting or (note and note["status"] == "conflict"):
                outcome = "conflict"
            elif not fact or fact.get("value") is None:
                outcome = "unknown"
            else:
                try:
                    outcome = "met" if evaluate_rule(fact["value"], rule["operator"], rule["value"]) else "unmet"
                except (TypeError, ValueError):
                    outcome = "conflict"
            results.append({
                "criterion_id": rule["id"],
                "label": rule.get("label") or rule["id"],
                "fact": rule["fact"],
                "expected": f"{rule['operator']} {rule['value']}",
                "actual": fact.get("value") if fact else None,
                "status": outcome,
                "fact_source_ref": fact.get("source_ref") if fact else None,
                "rule_source_id": source_id,
                "note": note["message"] if note and outcome in ("unknown", "conflict") else None,
            })
        by_scheme[scheme["scheme_id"]] = results
    update: dict[str, Any] = {"criterion_results_by_scheme": by_scheme, "events": add_event(state, "evaluate_criteria")}
    if state.get("mode", "apply") == "apply":
        update["criterion_results"] = by_scheme[state["scheme_id"]]
    return update


def scheme_source_id(state: BenefitsState, scheme: dict[str, Any]) -> str:
    snapshot = (state.get("source_snapshots") or {}).get(scheme.get("scheme_id", ""))
    return (snapshot or state["source_snapshot"])["source_id"]


def match_document_requirements(state: BenefitsState) -> dict[str, Any]:
    """The shared matcher (tools/document_rules.match_requirements) for each scheme's papers."""
    schemes = state.get("schemes") or [state["scheme"]]
    by_scheme = {}
    for scheme in schemes:
        source_id = scheme_source_id(state, scheme)
        matches = match_requirements(scheme["required_documents"], state.get("documents", []), state["actor_id"],
                                     state["as_of"])
        by_scheme[scheme.get("scheme_id", "")] = [{**match, "rule_source_id": source_id} for match in matches]
    return {
        "document_matches_by_scheme": by_scheme,
        "document_matches": by_scheme[schemes[0].get("scheme_id", "")],
        "events": add_event(state, "match_document_requirements"),
    }


def summarize_gaps(state: BenefitsState) -> dict[str, Any]:
    gaps = [f"Provide or confirm {m['required_type']} ({m['status']})" for m in state["document_matches"] if m["status"] != "usable"]
    return {"missing_inputs": gaps, "events": add_event(state, "summarize_document_gaps")}


MISSING_DETAIL = {
    "gender": "Your ID card's details do not say if you are a man or a woman. Add it as printed on the card.",
    "disability_percent": "Add the disability percentage, as printed on your disability certificate.",
}
DOCUMENT_GAP = {
    "missing": "Add your {word}.",
    "unconfirmed": "Your {word} is being read or is waiting for you to check it.",
    "expired": "Your {word} has expired. Add the new copy.",
}


def scheme_gaps(results: list[dict[str, Any]], matches: list[dict[str, Any]],
                notes: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """What to add or check, one line per paper and reason:
    {document_type, document_id, reason (missing, unconfirmed, expired, detail_missing, check), message}."""
    gaps: list[dict[str, Any]] = []

    def add(document_type: str | None, document_id: str | None, reason: str, message: str) -> None:
        if not any(gap["document_type"] == document_type and gap["reason"] == reason for gap in gaps):
            gaps.append({"document_type": document_type, "document_id": document_id, "reason": reason,
                         "message": message})

    for match in matches:
        if match["status"] != "usable":
            word = DOCUMENT_WORDS.get(match["required_type"], match["required_type"].replace("_", " "))
            add(match["required_type"], match["document_id"], match["status"],
                DOCUMENT_GAP[match["status"]].format(word=word))
    covered = {gap["document_type"] for gap in gaps}
    by_type = {match["required_type"]: match for match in matches}
    for result in results:
        if result["status"] not in ("unknown", "conflict"):
            continue
        document_type = FACT_DOCUMENT.get(result["fact"])
        word = DOCUMENT_WORDS.get(document_type or "", "papers")
        note = notes.get(result["fact"])
        if note:
            add(note["document_type"], note["document_id"],
                "check" if note["status"] == "conflict" else "detail_missing", note["message"])
        elif result["status"] == "conflict":
            add(document_type, by_type.get(document_type, {}).get("document_id"), "check",
                f"Your papers do not agree about this: {result['label'].lower()}. Please check your {word}.")
        elif document_type not in covered:
            match = by_type.get(document_type)
            if match and match["status"] == "usable":
                add(document_type, match["document_id"], "detail_missing",
                    MISSING_DETAIL.get(result["fact"], f"Add the missing detail to your {word}."))
            else:
                add(document_type, None, "missing", DOCUMENT_GAP["missing"].format(word=word))
    return gaps


SCREEN_STATUS = {"blocked_conflict": "please_check", "not_eligible_in_demo": "not_eligible",
                 "provisionally_eligible": "likely"}


def assess_scheme(state: BenefitsState, scheme: dict[str, Any]) -> dict[str, Any]:
    results = state["criterion_results_by_scheme"][scheme["scheme_id"]]
    matches = state["document_matches_by_scheme"][scheme["scheme_id"]]
    declarations = state.get("declarations") or {}
    missing = [f"Provide or confirm {m['required_type']} ({m['status']})" for m in matches if m["status"] != "usable"]
    missing += [f"Confirm {r['fact']}" for r in results if r["status"] == "unknown"]
    to_confirm = [{"key": item["key"], "label": item["label"], "confirmed": declarations.get(item["key"]) is True}
                  for item in scheme.get("declarations", [])]
    unanswered = [item for item in to_confirm if not item["confirmed"]]
    names_differ = OWNER_NAMES_DIFFER in state.get("contradictions", [])
    if names_differ:
        decision = "blocked_conflict"  # the papers may not all be the same person's
    elif any(r["status"] == "unmet" for r in results):
        decision = "not_eligible_in_demo"
    elif any(r["status"] == "conflict" for r in results):
        decision = "blocked_conflict"
    elif missing or unanswered:
        decision = "needs_input"
    else:
        decision = "provisionally_eligible"
    gaps = scheme_gaps(results, matches, state.get("fact_notes", {}))
    if names_differ or (decision == "blocked_conflict" and not gaps):
        gaps.append({"document_type": None, "document_id": None, "reason": "check",
                     "message": "Your papers show different names. Please check that they are all yours."})
    status = SCREEN_STATUS.get(decision) or ("add_documents" if missing else "likely")
    return {
        "scheme_id": scheme["scheme_id"],
        "name": scheme["name"],
        "summary": scheme.get("summary"),
        "benefit": scheme.get("benefit"),
        "exclusive_group": scheme.get("exclusive_group"),
        "source_id": state["source_snapshots"][scheme["scheme_id"]]["source_id"],
        "decision": decision,
        "status": status,
        "criteria": results,
        "documents": matches,
        "gaps": gaps,
        "to_confirm": to_confirm,
        "missing_inputs": missing + [f"Confirm: {item['label']}" for item in unanswered],
    }


def reconcile_assessment(state: BenefitsState) -> dict[str, Any]:
    assessments = [assess_scheme(state, scheme) for scheme in state["schemes"]]
    if state["mode"] == "screen":
        notes = []
        for group, sentence in EXCLUSIVE_GROUPS.items():
            if sum(item["exclusive_group"] == group and item["status"] == "likely" for item in assessments) > 1:
                notes.append(sentence)
        return {"scheme_assessments": assessments, "notes": notes, "status": "screened", "route": "screened",
                "events": add_event(state, f"reconcile_assessment:screened_{len(assessments)}")}
    one = assessments[0]
    status = one["decision"]
    missing = list(state.get("missing_inputs", []))
    missing += [item for item in one["missing_inputs"] if item not in missing]
    route = "continue" if status == "provisionally_eligible" else "stop"
    assessment = {
        "case_id": state["case_id"],
        "scheme_id": state["scheme_id"],
        "source_id": state["source_snapshot"]["source_id"],
        "as_of": state["as_of"],
        "status": status,
        "criterion_results": state["criterion_results"],
        "document_matches": state["document_matches"],
        "missing_inputs": missing,
        "contradictions": state.get("contradictions", []),
    }
    return {
        "assessment": assessment,
        "scheme_assessments": assessments,
        "missing_inputs": missing,
        "status": status,
        "route": route,
        "events": add_event(state, f"reconcile_assessment:{status}"),
    }


def evidence_of(state: BenefitsState, document_ids: list[str]) -> str:
    """A short fingerprint of the papers and facts the package rests on: if any of them changes,
    the package's action_id changes too, so an old approval can't be applied to new papers."""
    chosen = sorted((document.get("document_id"), document.get("confirmed_fields", {}))
                    for document in state.get("documents", []) if document.get("document_id") in document_ids)
    payload = {"documents": chosen, "facts": state.get("facts", {}), "declarations": state.get("declarations") or {}}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]


def draft_handoff(state: BenefitsState) -> dict[str, Any]:
    scheme = state["scheme"]
    matches = state["document_matches"]
    selected_documents = [m["document_id"] for m in matches]
    apply_at = scheme.get("apply_at", "the office that runs the scheme")
    declared = [item["label"] for item in scheme.get("declarations", [])]
    checklist = [  # shown to the person as it is: plain words
        f"Take the papers listed, the originals and one photocopy of each, to {apply_at}",
        *([f"Sign the declarations: {'; '.join(declared)}"] if declared else []),
        "Ask for a receipt and keep its reference number",
        "This is a made-up demo scheme: for a real scheme, ask the office how to apply first",
    ]
    proposal = {
        "case_id": state["case_id"],
        "scheme_id": state["scheme_id"],
        "scheme_name": scheme["name"],
        "assessment_as_of": state["as_of"],
        "source_id": state["source_snapshot"]["source_id"],
        "document_ids": selected_documents,
        "documents": [{"document_type": m["required_type"], "document_id": m["document_id"]} for m in matches],
        "declarations": declared,
        "evidence": evidence_of(state, selected_documents),
        "checklist": checklist,
        "tasks": [{"title": f"Apply for {scheme['name']} at {apply_at}" if scheme.get("apply_at")
                   else "Review and submit demo pension application manually", "owner_id": state["actor_id"]}],
        "warning": "Fictional demo scheme. This is not a real eligibility decision or application.",
    }
    proposal["action_id"] = action_id_for(proposal)
    return {"package_proposal": proposal, "events": add_event(state, "draft_handoff")}


def action_id_for(proposal: dict[str, Any]) -> str:
    payload = {key: value for key, value in proposal.items() if key != "action_id"}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def quality_gate(state: BenefitsState) -> dict[str, Any]:
    proposal = state["package_proposal"]
    source_id = state["source_snapshot"]["source_id"]
    declarations = state.get("declarations") or {}
    good = (
        state["assessment"]["status"] == "provisionally_eligible"
        and all(r["status"] == "met" and r["rule_source_id"] == source_id and r["fact_source_ref"] for r in state["criterion_results"])
        and all(m["status"] == "usable" and m["document_id"] for m in state["document_matches"])
        and all(declarations.get(item["key"]) is True for item in state["scheme"].get("declarations", []))
        and proposal["source_id"] == source_id
        and bool(proposal["document_ids"])
    )
    return {
        "status": "awaiting_review" if good else "blocked_quality_gate",
        "route": "continue" if good else "stop",
        "errors": [] if good else ["Proposal failed citation, document, or assessment checks"],
        "events": add_event(state, "quality_gate:passed" if good else "quality_gate:failed"),
    }


def human_review(state: BenefitsState) -> dict[str, Any]:
    decision = state.get("review_decision")
    if not decision and state.get("use_interrupt"):
        decision = interrupt({
            "case_id": state["case_id"],
            "assessment": state["assessment"],
            "proposal": state["package_proposal"],
            "question": "Approve, edit the task title, or reject this handoff package",
        })
    if not decision:
        return {"status": "awaiting_review", "route": "stop", "events": add_event(state, "human_review:pending")}
    if decision.get("reviewer_id") != state["actor_id"]:
        return {
            "status": "rejected",
            "route": "stop",
            "errors": ["Only the case owner can approve this demo package"],
            "events": add_event(state, "human_review:unauthorized"),
        }
    if decision.get("action") == "reject":
        return {"status": "rejected", "route": "stop", "events": add_event(state, "human_review:rejected")}
    if decision.get("action") not in {"approve", "edit"}:
        return {"status": "awaiting_review", "route": "stop", "errors": ["Invalid decision"], "events": add_event(state, "human_review:invalid")}
    return {"review_decision": decision, "route": "continue", "events": add_event(state, "human_review:accepted")}


def revalidate_review(state: BenefitsState) -> dict[str, Any]:
    decision = state["review_decision"]
    proposal = dict(state["package_proposal"])
    if state["case_version"] != state["expected_case_version"]:
        return {"status": "blocked_stale_case", "route": "stop", "errors": ["Case changed during review"], "events": add_event(state, "revalidate_review:stale")}
    edits = decision.get("edits", {})
    if set(edits) - {"task_title"}:
        return {"status": "awaiting_review", "route": "stop", "errors": ["Only task_title can be edited in this demo"], "events": add_event(state, "revalidate_review:invalid_edit")}
    if "task_title" in edits:
        title = str(edits["task_title"]).strip()
        if not title:
            return {"status": "awaiting_review", "route": "stop", "errors": ["Task title cannot be empty"], "events": add_event(state, "revalidate_review:empty_title")}
        proposal["tasks"] = [{**proposal["tasks"][0], "title": title}]
        proposal["action_id"] = action_id_for(proposal)
    return {"package_proposal": proposal, "route": "continue", "events": add_event(state, "revalidate_review:passed")}


def commit_handoff(state: BenefitsState) -> dict[str, Any]:
    proposal = state["package_proposal"]
    action_id = proposal["action_id"]
    if state.get("committed_action_id") == action_id:
        return {"events": add_event(state, "commit_handoff:already_committed")}
    package = {**proposal, "status": "approved_for_manual_handoff", "approved_by": state["actor_id"]}
    return {
        "application_package": package,
        "created_tasks": proposal["tasks"],
        "committed_action_id": action_id,
        "events": add_event(state, "commit_handoff:success"),
    }


def finalize_case(state: BenefitsState) -> dict[str, Any]:
    return {
        "status": "ready_for_manual_handoff",
        "route": "done",
        "events": add_event(state, "finalize_case"),
    }


def route_step(state: BenefitsState) -> str:
    return state.get("route", "stop")


def build_document_match_subgraph():
    graph = StateGraph(BenefitsState)
    graph.add_node("match_document_requirements", match_document_requirements)
    graph.add_node("summarize_gaps", summarize_gaps)
    graph.add_edge(START, "match_document_requirements")
    graph.add_edge("match_document_requirements", "summarize_gaps")
    graph.add_edge("summarize_gaps", END)
    return graph.compile()


def build_benefits_graph(checkpointer: Any = None):
    graph = StateGraph(BenefitsState)
    graph.add_node("load_case_context", load_case_context)
    graph.add_node("resolve_scheme", resolve_scheme)
    graph.add_node("retrieve_scheme_evidence", retrieve_scheme_evidence)
    graph.add_node("validate_rule_set", validate_rule_set)
    graph.add_node("collect_confirmed_facts", collect_confirmed_facts)
    graph.add_node("link_documents", link_documents)
    graph.add_node("evaluate_criteria", evaluate_criteria)
    graph.add_node("match_documents", build_document_match_subgraph())
    graph.add_node("reconcile_assessment", reconcile_assessment)
    graph.add_node("draft_handoff", draft_handoff)
    graph.add_node("quality_gate", quality_gate)
    graph.add_node("human_review", human_review)
    graph.add_node("revalidate_review", revalidate_review)
    graph.add_node("commit_handoff", commit_handoff)
    graph.add_node("finalize_case", finalize_case)

    graph.add_edge(START, "load_case_context")
    graph.add_conditional_edges("load_case_context", route_step, {"continue": "resolve_scheme", "stop": END})
    graph.add_conditional_edges("resolve_scheme", route_step, {"continue": "retrieve_scheme_evidence", "stop": END})
    graph.add_conditional_edges("retrieve_scheme_evidence", route_step, {"continue": "validate_rule_set", "stop": END})
    graph.add_conditional_edges("validate_rule_set", route_step, {"continue": "collect_confirmed_facts", "stop": END})
    graph.add_edge("collect_confirmed_facts", "link_documents")
    graph.add_edge("link_documents", "evaluate_criteria")
    graph.add_edge("evaluate_criteria", "match_documents")
    graph.add_edge("match_documents", "reconcile_assessment")
    graph.add_conditional_edges("reconcile_assessment", route_step,
                                {"continue": "draft_handoff", "stop": END, "screened": END})
    graph.add_edge("draft_handoff", "quality_gate")
    graph.add_conditional_edges("quality_gate", route_step, {"continue": "human_review", "stop": END})
    graph.add_conditional_edges("human_review", route_step, {"continue": "revalidate_review", "stop": END})
    graph.add_conditional_edges("revalidate_review", route_step, {"continue": "commit_handoff", "stop": END})
    graph.add_edge("commit_handoff", "finalize_case")
    graph.add_edge("finalize_case", END)
    return graph.compile(checkpointer=checkpointer)


# Plain invoke(initial_state) works when review_decision is already supplied.
if __name__ == '__main__':
    benefits_graph = build_benefits_graph()
    if not os.path.exists('graphs_img/benefits_graph.png'):
        from IPython.display import display, Image
        img = Image(benefits_graph.get_graph().draw_mermaid_png())
        with open('graphs_img/benefits_graph.png', 'wb') as file:
            file.write(img.data)
