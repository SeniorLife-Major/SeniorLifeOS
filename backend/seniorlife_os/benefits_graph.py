"""Benefits case graph using confirmed vault records and a fictional catalogue.

This graph does not upload documents or submit a government application. It
assesses a curated demo rule set, explains evidence gaps, and creates a
reviewed handoff package in graph state.
"""

from __future__ import annotations

import os
import hashlib
import json
from datetime import date
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .demo_tools import DEMO_SCHEMES, evaluate_rule, get_scheme


class BenefitsState(TypedDict, total=False):
    # Handwritten inputs
    actor_id: str
    case_id: str
    case_version: int
    expected_case_version: int
    scheme_id: str
    need_text: str
    jurisdiction: str
    as_of: str
    profile_facts: dict[str, dict[str, Any]]
    documents: list[dict[str, Any]]
    review_decision: dict[str, Any]
    use_interrupt: bool
    use_llm: bool
    simulate_source_unavailable: bool

    # Graph outputs / control
    scheme: dict[str, Any]
    source_snapshot: dict[str, Any]
    facts: dict[str, dict[str, Any]]
    contradictions: list[str]
    criterion_results: list[dict[str, Any]]
    document_matches: list[dict[str, Any]]
    missing_inputs: list[str]
    assessment: dict[str, Any]
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
    errors = []
    for key in ("actor_id", "case_id", "jurisdiction"):
        if not state.get(key):
            errors.append(f"{key} is required")
    if state.get("case_version") != state.get("expected_case_version"):
        errors.append("Case version changed; start a new assessment run")
    try:
        date.fromisoformat(state.get("as_of", ""))
    except ValueError:
        errors.append("as_of must be an ISO date (YYYY-MM-DD)")
    return {
        "errors": errors,
        "status": "blocked" if errors else "evaluating",
        "route": "stop" if errors else "continue",
        "events": add_event(state, "load_case_context"),
    }


def resolve_scheme(state: BenefitsState) -> dict[str, Any]:
    # The selection agent is intentionally bounded to a curated local list.
    scheme_id = state.get("scheme_id")
    if not scheme_id and state.get("use_llm"):
        try:
            from .llm_agents import choose_scheme

            candidates = [scheme for scheme in DEMO_SCHEMES.values() if scheme["jurisdiction"] == state["jurisdiction"]]
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
    if scheme["jurisdiction"] != state["jurisdiction"]:
        return {
            "status": "needs_scheme_choice",
            "route": "stop",
            "warnings": ["The selected scheme does not cover this jurisdiction"],
            "events": add_event(state, "resolve_scheme:wrong_jurisdiction"),
        }
    return {
        "scheme": scheme,
        "scheme_id": scheme_id,
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
    return {
        "source_snapshot": state["scheme"]["source"].copy(),
        "route": "continue",
        "events": add_event(state, "retrieve_scheme_evidence:success"),
    }


def validate_rule_set(state: BenefitsState) -> dict[str, Any]:
    scheme = state["scheme"]
    source = state["source_snapshot"]
    criteria = scheme.get("criteria", [])
    try:
        source_age_days = (date.fromisoformat(state["as_of"]) - date.fromisoformat(source["retrieved_at"])).days
    except (KeyError, ValueError):
        source_age_days = 9999
    valid = (
        source.get("approved_for_demo") is True
        and bool(source.get("source_id"))
        and 0 <= source_age_days <= 365
        and bool(criteria)
        and all(c.get("id") and c.get("fact") and c.get("operator") in {">=", "<=", "=="} for c in criteria)
    )
    if not valid:
        return {
            "status": "blocked_source_review",
            "route": "stop",
            "errors": ["The demo rule set lacks validated source or criterion fields"],
            "events": add_event(state, "validate_rule_set:failed"),
        }
    return {"route": "continue", "events": add_event(state, "validate_rule_set:passed")}


def collect_confirmed_facts(state: BenefitsState) -> dict[str, Any]:
    facts: dict[str, dict[str, Any]] = {}
    contradictions = []
    document_names = set()
    as_of = date.fromisoformat(state["as_of"])
    for key, fact in state.get("profile_facts", {}).items():
        if fact.get("confirmed") and fact.get("source_ref"):
            facts[key] = {"value": fact.get("value"), "source_ref": fact["source_ref"]}
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
        name = document.get("confirmed_fields", {}).get("name", {}).get("value")
        if name:
            document_names.add(str(name).strip().casefold())
        for field_name, field in document.get("confirmed_fields", {}).items():
            if field_name not in {"annual_income", "residence_state", "date_of_birth"}:
                continue
            new_fact = {"value": field.get("value"), "source_ref": field.get("source_ref")}
            if not new_fact["source_ref"]:
                continue
            if field_name in facts and facts[field_name]["value"] != new_fact["value"]:
                contradictions.append(f"Conflicting confirmed values for {field_name}")
            else:
                facts[field_name] = new_fact
    if len(document_names) > 1:
        contradictions.append("Confirmed documents have different owner names")
    if "date_of_birth" in facts:
        try:
            dob = date.fromisoformat(str(facts["date_of_birth"]["value"]))
            as_of = date.fromisoformat(state["as_of"])
            age = as_of.year - dob.year - ((as_of.month, as_of.day) < (dob.month, dob.day))
            age_fact = {"value": age, "source_ref": facts["date_of_birth"]["source_ref"]}
            if "age" in facts and facts["age"]["value"] != age:
                contradictions.append("Profile age conflicts with confirmed date of birth")
            else:
                facts["age"] = age_fact
        except ValueError:
            contradictions.append("Confirmed date of birth is invalid")
    return {
        "facts": facts,
        "contradictions": contradictions,
        "events": add_event(state, "collect_confirmed_facts"),
    }


def evaluate_criteria(state: BenefitsState) -> dict[str, Any]:
    results = []
    for rule in state["scheme"]["criteria"]:
        fact = state["facts"].get(rule["fact"])
        if any(rule["fact"] in item or (rule["fact"] == "age" and "age" in item) for item in state["contradictions"]):
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
            "fact": rule["fact"],
            "expected": f"{rule['operator']} {rule['value']}",
            "actual": fact.get("value") if fact else None,
            "status": outcome,
            "fact_source_ref": fact.get("source_ref") if fact else None,
            "rule_source_id": state["source_snapshot"]["source_id"],
        })
    return {"criterion_results": results, "events": add_event(state, "evaluate_criteria")}


def match_document_requirements(state: BenefitsState) -> dict[str, Any]:
    matches = []
    today = date.fromisoformat(state["as_of"])
    required_fields = {
        "identity": {"name", "date_of_birth"},
        "income_certificate": {"name", "annual_income", "issue_date"},
        "residence_proof": {"name", "residence_state", "issue_date"},
    }
    for required_type in state["scheme"]["required_documents"]:
        candidates = [d for d in state.get("documents", []) if d.get("type") == required_type and d.get("owner_id") == state["actor_id"]]
        status = "missing"
        selected_id = None
        if candidates:
            ranked = []
            for candidate in candidates:
                fields = candidate.get("confirmed_fields", {})
                complete = all(fields.get(key, {}).get("value") is not None and fields.get(key, {}).get("source_ref") for key in required_fields[required_type])
                candidate_status = "usable" if candidate.get("status") == "confirmed" and complete else "unconfirmed"
                expiry = candidate.get("expiry_date")
                if expiry:
                    try:
                        if date.fromisoformat(expiry) < today:
                            candidate_status = "expired"
                    except ValueError:
                        candidate_status = "unconfirmed"
                ranked.append((candidate_status, candidate))
            rank_order = {"usable": 0, "unconfirmed": 1, "expired": 2}
            status, chosen = min(ranked, key=lambda pair: rank_order[pair[0]])
            selected_id = chosen.get("document_id")
        matches.append({
            "required_type": required_type,
            "document_id": selected_id,
            "status": status,
            "rule_source_id": state["source_snapshot"]["source_id"],
        })
    return {"document_matches": matches, "events": add_event(state, "match_document_requirements")}


def summarize_gaps(state: BenefitsState) -> dict[str, Any]:
    gaps = [f"Provide or confirm {m['required_type']} ({m['status']})" for m in state["document_matches"] if m["status"] != "usable"]
    return {"missing_inputs": gaps, "events": add_event(state, "summarize_document_gaps")}


def reconcile_assessment(state: BenefitsState) -> dict[str, Any]:
    results = state["criterion_results"]
    missing = list(state.get("missing_inputs", []))
    missing += [f"Confirm {r['fact']}" for r in results if r["status"] == "unknown"]
    if state.get("contradictions") or any(r["status"] == "conflict" for r in results):
        status, route = "blocked_conflict", "stop"
    elif any(r["status"] == "unmet" for r in results):
        status, route = "not_eligible_in_demo", "stop"
    elif missing:
        status, route = "needs_input", "stop"
    else:
        status, route = "provisionally_eligible", "continue"
    assessment = {
        "case_id": state["case_id"],
        "scheme_id": state["scheme_id"],
        "source_id": state["source_snapshot"]["source_id"],
        "as_of": state["as_of"],
        "status": status,
        "criterion_results": results,
        "document_matches": state["document_matches"],
        "missing_inputs": missing,
        "contradictions": state.get("contradictions", []),
    }
    return {
        "assessment": assessment,
        "missing_inputs": missing,
        "status": status,
        "route": route,
        "events": add_event(state, f"reconcile_assessment:{status}"),
    }


def draft_handoff(state: BenefitsState) -> dict[str, Any]:
    selected_documents = [m["document_id"] for m in state["document_matches"]]
    proposal = {
        "case_id": state["case_id"],
        "scheme_id": state["scheme_id"],
        "scheme_name": state["scheme"]["name"],
        "assessment_as_of": state["as_of"],
        "source_id": state["source_snapshot"]["source_id"],
        "document_ids": selected_documents,
        "checklist": [
            "Review the provisional criteria and source",
            "Check the confirmed documents listed below",
            "For a real scheme, verify its official application channel before applying",
            "Record the receipt/reference number after submission",
        ],
        "tasks": [{"title": "Review and submit demo pension application manually", "owner_id": state["actor_id"]}],
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
    good = (
        state["assessment"]["status"] == "provisionally_eligible"
        and all(r["status"] == "met" and r["rule_source_id"] == source_id and r["fact_source_ref"] for r in state["criterion_results"])
        and all(m["status"] == "usable" and m["document_id"] for m in state["document_matches"])
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
    graph.add_edge("collect_confirmed_facts", "evaluate_criteria")
    graph.add_edge("evaluate_criteria", "match_documents")
    graph.add_edge("match_documents", "reconcile_assessment")
    graph.add_conditional_edges("reconcile_assessment", route_step, {"continue": "draft_handoff", "stop": END})
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

