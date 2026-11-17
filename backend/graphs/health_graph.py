"""Health request graph (plan §5, Module B): emergency check -> understand -> route -> where.

    validate_request -> safety_check (fixed rules) -> understand_request (AI model, or local words)
        -> emergency            the rules found a red flag, or the model saw an emergency
        -> confirm_emergency    the rules found one but the model thinks it is not happening now:
                                the person is asked (in the model's words) whether they want to
                                call emergency services; only they can clear it
        -> map_specialty        find_doctor: the kind of doctor, from a fixed list
            -> locate           where to look: the phone's location ("near me"), or a place named
                                in the request looked up on Geoapify (tools/geocode.py)
                -> ask_location missing, not found, or one name for far-apart places: one
                                question (a place, the phone's location, or one of the options)
        -> ask_clarification    unclear: one question, then the answer is checked from the start
        -> stop                 a task this graph doesn't handle yet

Safety rule (plan §5.2): the emergency check is deterministic and runs first. The model is a second
check that can add an emergency the rules missed, never remove one: when it disagrees with the
rules, the emergency numbers are still shown and the person decides. Without the model (no
consent, no key, an error), the rule finding stands as it is.

A health request is medical data, so the model reads it only with use_llm and
medical_cloud_consent, redacted first (as for medical documents, Decision #29). Without them the
request is understood on this machine with a few words per specialty (tools/specialties.py).

Where to look (step 2a): the model says whether the person means where they are now, a named
place, or home (search_near). The phone's location is used only when they mean where they are
now, or said nothing about where; without the model, only clear "near me" words count, and
anything else is asked (a request may name a place this machine can't recognise). Finding,
checking and ranking doctors there comes next; this graph ends with the place.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from ..tools.geocode import GeocodeError, device_location, find_place, search_near_from_text
from ..tools.red_flags import emergency_message, emergency_numbers, find_red_flags
from ..tools.specialties import GENERAL_PHYSICIAN, SPECIALTIES, specialty_from_text
from ..utils.checkpoints import is_encrypted
from ..utils.tracing import install_safe_client

MAX_QUERY_CHARS = 2000
CLARIFYING_QUESTION = "Which kind of doctor do you need, or what problem would you like help with?"
# Asked when the model doubts a rule finding; the model words it in the person's language, this
# is the fallback when it gives none.
CALL_QUESTION = "Do you want to call emergency services (112 or 108) now?"
VAGUE_NEED = "The need is not specific, so a general physician is the safest first doctor to see"
NOT_HANDLED = {
    "appointment": "Booking appointments",
    "medicine": "Medicines and reminders",
    "documents": "Documents",
    "benefits": "Benefits and schemes",
    "other": "This request",
}
HERE_LABEL = "your current location"
LOCATION_QUESTIONS = {
    "current_location": "Please share your current location, or tell me the area and city you are in.",
    "home": "Which area is your home in? Please tell me the area and the city.",
    "where": "Which area should I look for a doctor in? Please tell me the area and the city.",
    "where_or_here": "Should I look near where you are now? Say \"near me\", or tell me the area and the city.",
    "not_found": "I could not find that place. Please tell me the area and the city.",
    "ambiguous": "I found more than one place with that name: {options}. Which one do you mean?",
    "unavailable": "I can't look up places just now. Please share your current location.",
}
LOCATION_NOT_FOUND = "The place could not be found. Please start again with the area and the city."


class HealthState(TypedDict, total=False):
    # Inputs
    actor_id: str
    query: str  # what the senior said or typed
    owner_name: str  # hidden from the model's prompt
    use_llm: bool
    medical_cloud_consent: bool  # the senior allows the cloud model to read health requests
    use_interrupt: bool
    emergency_answer: dict[str, Any]  # {"call_emergency": bool}, the person's answer to the emergency question
    clarification_answer: str  # the person's answer to the clarifying question
    current_location: dict[str, float]  # {"lat", "lon"} from the phone, shared with the person's permission
    location_answer: Any  # the answer to the location question: a place in words, or {"lat", "lon"}

    # Outputs / control
    red_flags: list[str]  # found by the fixed rules
    model_emergency: bool | None  # the model's second opinion (None: the model was not used)
    model_signs: list[str]  # the emergency signs the model saw (it may see some the rules missed)
    emergency_source: str  # rules, rules+model, model, person
    emergency: dict[str, Any]
    reading_source: str  # model or local
    intent: str
    specialty: str | None
    specialty_label: str
    urgency: str
    preferences: dict[str, Any]
    reason: str
    question: str
    emergency_question: str  # asked when the model doubts the rules: call emergency services now?
    clarified: bool
    search_near: str  # current_location, named_place, home, not_said (model); unknown (local words)
    search_location: dict[str, Any]  # where to look: lat, lon, label, source (phone, request, answer)
    location_options: list[dict[str, Any]]  # far-apart places sharing the name asked about
    location_problem: str  # why the location question was asked
    location_query: str  # the place text looked up
    location_asked: bool
    events: list[str]
    warnings: list[str]
    errors: list[str]
    status: str
    route: str


def add_event(state: HealthState, name: str) -> list[str]:
    return [*state.get("events", []), name]


def validate_request(state: HealthState) -> dict[str, Any]:
    errors = []
    query = state.get("query")
    if not state.get("actor_id"):
        errors.append("actor_id is required")
    if not isinstance(query, str) or not query.strip():
        errors.append("Say or type what you need")
    elif len(query) > MAX_QUERY_CHARS:
        errors.append(f"The request must be at most {MAX_QUERY_CHARS} characters")
    return {
        "errors": errors,
        "status": "rejected" if errors else "processing",
        "route": "stop" if errors else "continue",
        "events": add_event(state, "validate_request"),
    }


def safety_check(state: HealthState) -> dict[str, Any]:
    """The fixed emergency rules, before anything else (plan §5.2). No model, no network."""
    flags = find_red_flags(state["query"])
    return {"red_flags": flags, "route": "continue",
            "events": add_event(state, f"safety_check:{'+'.join(flags) or 'none'}")}


def next_step(intent: str, clarified: bool) -> dict[str, Any]:
    """Where a request goes once it is known not to be an emergency."""
    if intent == "find_doctor":
        return {"route": "find_doctor"}
    if intent == "unclear":
        if clarified:  # one question only; the person can start again in their own words
            return {"route": "stop", "status": "not_understood"}
        return {"route": "clarify"}
    return {"route": "stop", "status": "not_available",
            "note": f"{NOT_HANDLED.get(intent, NOT_HANDLED['other'])} can't be handled here yet"}


def understand_request(state: HealthState) -> dict[str, Any]:
    """What the person wants, read by the AI model (with consent) or by local words. Also the
    model's second opinion on the rule's emergency finding."""
    query, flags = state["query"], state.get("red_flags", [])
    warnings = list(state.get("warnings", []))
    reading = None
    if state.get("use_llm") and not state.get("medical_cloud_consent"):
        warnings.append("The AI model was not used: health requests need medical consent")
    elif state.get("use_llm"):
        try:
            from ..agents.llm_agents import understand_health_request

            known = {"NAME": state["owner_name"]} if state.get("owner_name") else None
            reading = understand_health_request(query, flags, known)
        except Exception as exc:  # no model, no key, network, or redaction failure: understood locally
            warnings.append(f"AI reading unavailable ({type(exc).__name__}); the request was read on this machine")
    if reading is not None:
        update = {
            "reading_source": "model",
            "model_emergency": reading.emergency_now,
            "model_signs": list(reading.emergency_signs),
            "intent": reading.intent,
            "specialty": None if reading.specialty == "unsure" else reading.specialty,
            "urgency": reading.urgency,
            "preferences": reading.preferences.model_dump(),
            "search_near": reading.search_near,
            "reason": reading.reason,
            "question": reading.clarifying_question or CLARIFYING_QUESTION,
            "emergency_question": reading.emergency_question or CALL_QUESTION,
        }
    else:
        specialty = specialty_from_text(query)
        update = {
            "reading_source": "local",
            "model_emergency": None,
            "model_signs": [],
            "intent": "find_doctor" if specialty else "unclear",
            "specialty": specialty,
            "urgency": "routine",
            "preferences": {},
            "search_near": search_near_from_text(query),
            "reason": "Read on this machine from the words used",
            "question": CLARIFYING_QUESTION,
            "emergency_question": CALL_QUESTION,
        }
    model_emergency = update["model_emergency"]
    if flags and model_emergency is False:
        step = {"route": "confirm_emergency"}  # the model doubts the rule: the person decides
    elif flags:
        step = {"route": "emergency", "emergency_source": "rules+model" if model_emergency else "rules"}
    elif model_emergency:
        step = {"route": "emergency", "emergency_source": "model"}  # the model may add one the rules missed
    else:
        step = next_step(update["intent"], state.get("clarified", False))
    note = step.pop("note", None)
    return {
        **update,
        **step,
        "warnings": [*warnings, note] if note else warnings,
        "events": add_event(state, f"understand_request:{update['reading_source']}:{update['intent']}"),
    }


def emergency_details(state: HealthState) -> dict[str, Any]:
    """Which signs were found, and the numbers and message for them. The model's signs count
    too, so self-harm it noticed in words the rules don't know still shows the helpline."""
    flags = list(state.get("red_flags", []))
    signs = [*flags, *(sign for sign in state.get("model_signs", []) if sign not in flags)]
    return {"red_flags": flags, "model_signs": list(state.get("model_signs", [])),
            "numbers": emergency_numbers(signs), "message": emergency_message(signs)}


def emergency(state: HealthState) -> dict[str, Any]:
    """Stop the normal flow and show the emergency numbers."""
    source = state.get("emergency_source", "rules")
    return {
        "status": "emergency",
        "emergency": {**emergency_details(state), "source": source},
        "route": "done",
        "events": add_event(state, f"emergency:{source}"),
    }


def confirm_emergency(state: HealthState) -> dict[str, Any]:
    """The rules found a red flag but the model thinks it is not happening now. The person is asked,
    in the model's words, whether they want to call emergency services; the numbers are shown
    anyway. Only a clear "no" ({"call_emergency": false}) goes on; anything else is an emergency."""
    answer = state.get("emergency_answer")
    question = state.get("emergency_question") or CALL_QUESTION
    if not answer and state.get("use_interrupt"):
        # No side effects before interrupt: this node can safely run again on resume.
        answer = interrupt({**emergency_details(state), "question": question})
    if not answer:
        return {
            "status": "needs_emergency_confirmation",
            "emergency": emergency_details(state),
            "question": question,
            "route": "stop",
            "events": add_event(state, "confirm_emergency:pending"),
        }
    if not isinstance(answer, dict) or answer.get("call_emergency") is not False:
        return {"emergency_source": "person", "route": "emergency",
                "events": add_event(state, "confirm_emergency:call")}
    step = next_step(state.get("intent", "unclear"), state.get("clarified", False))
    note = step.pop("note", None)
    return {
        **step,
        "emergency_source": "person",
        "emergency_answer": {},  # answers this finding only: a later red flag is asked about again
        **({"warnings": [*state.get("warnings", []), note]} if note else {}),
        "events": add_event(state, "confirm_emergency:no_call"),
    }


def ask_clarification(state: HealthState) -> dict[str, Any]:
    """Ask one question. The answer is added to the request, which is checked again from the
    emergency rules on (an answer can describe an emergency too)."""
    answer = state.get("clarification_answer")
    question = state.get("question") or CLARIFYING_QUESTION
    if not answer and state.get("use_interrupt"):
        answer = interrupt({"question": question})
    if not isinstance(answer, str) or not answer.strip():
        return {"status": "needs_clarification", "question": question, "route": "stop",
                "events": add_event(state, "ask_clarification:pending")}
    query = f"{state['query']}\n{answer.strip()}"
    if len(query) > MAX_QUERY_CHARS:
        return {"status": "rejected", "errors": [f"The request must be at most {MAX_QUERY_CHARS} characters"],
                "route": "stop", "events": add_event(state, "ask_clarification:too_long")}
    return {"query": query, "clarified": True, "clarification_answer": "", "route": "again",
            "events": add_event(state, "ask_clarification:answered")}


def map_specialty(state: HealthState) -> dict[str, Any]:
    """The kind of doctor, always one of SPECIALTIES. A vague need gets a general physician."""
    specialty = state.get("specialty")
    reason = state.get("reason", "")
    if specialty not in SPECIALTIES:
        specialty, reason = GENERAL_PHYSICIAN, VAGUE_NEED
    return {
        "specialty": specialty,
        "specialty_label": SPECIALTIES[specialty],
        "reason": reason,
        "status": "specialty_found",
        "route": "locate",
        "events": add_event(state, f"map_specialty:{specialty}"),
    }


def _look_up(text: str, phone: dict[str, float] | None, warnings: list[str]):
    """Look a place up: (place, problem, options). A service failure becomes a warning, and the
    person is asked to share their location instead."""
    try:
        answer = find_place(text, near=phone)
    except GeocodeError as exc:  # its message never holds the key (tools/geocode.py)
        warnings.append(f"{exc}; the person is asked instead")
        return None, "unavailable", []
    if answer["status"] == "found":
        return answer["place"], "", []
    if answer["status"] == "ambiguous":
        return None, "ambiguous", answer["options"]
    return None, "not_found", []


def _from_answer(state: HealthState, phone: dict[str, float] | None, warnings: list[str]):
    """The answer to the location question: (place, problem, options, text looked up)."""
    answer = state.get("location_answer")
    if isinstance(answer, dict):  # the phone's location, shared after the question
        shared = device_location(answer)
        return ({**shared, "label": HERE_LABEL, "source": "phone"}, "", [], "") if shared else (None, "where", [], "")
    text = answer.strip() if isinstance(answer, str) else ""
    for option in state.get("location_options", []):
        if text.casefold() == option["label"].casefold():  # one of the places offered
            return {**option, "source": "answer"}, "", [], ""
    if phone and search_near_from_text(text) == "current_location":  # "near me", "yahin"
        return {**phone, "label": HERE_LABEL, "source": "phone"}, "", [], ""
    if state.get("location_problem") == "ambiguous" and state.get("location_query"):
        text = f"{state['location_query']}, {text}"  # "Andheri" + "Mumbai"
    place, problem, options = _look_up(text, phone, warnings)
    return ({**place, "source": "answer"} if place else None), problem, options, text


def locate(state: HealthState) -> dict[str, Any]:
    """Where to look for the doctor (step 2a).

    - "near me" (or nothing said about where) and the phone's location is known: use it.
    - a place named in the request: look it up; far-apart places with that name are asked about,
      unless one is near the phone.
    - otherwise, or if the lookup fails: ask one question (ask_location).
    Without the model only "near me" words count: a request that may name a place is asked about,
    never searched near the phone by guess."""
    phone = device_location(state.get("current_location"))
    near = state.get("search_near") or "unknown"
    area = (state.get("preferences") or {}).get("area")
    area = area.strip() if isinstance(area, str) else ""
    warnings = list(state.get("warnings", []))
    place, problem, options, looked_up = None, "", [], ""
    if state.get("location_asked"):
        place, problem, options, looked_up = _from_answer(state, phone, warnings)
    elif near == "current_location" and phone:
        place = {**phone, "label": HERE_LABEL, "source": "phone"}
    elif area and near != "unknown":
        looked_up = area
        place, problem, options = _look_up(area, phone, warnings)
        place = {**place, "source": "request"} if place else None
    elif near == "not_said" and phone:
        place = {**phone, "label": HERE_LABEL, "source": "phone"}
    elif near in ("current_location", "home"):
        problem = near
    else:
        problem = "where_or_here" if phone else "where"

    if place:
        return {"search_location": place, "location_options": [], "status": "location_found", "route": "done",
                "warnings": warnings, "events": add_event(state, f"locate:{place['source']}")}
    if state.get("location_asked"):  # one question only
        return {"status": "location_not_found", "route": "stop", "warnings": [*warnings, LOCATION_NOT_FOUND],
                "events": add_event(state, f"locate:{problem}:after_answer")}
    question = LOCATION_QUESTIONS[problem]
    if problem == "ambiguous":
        question = question.format(options="; ".join(option["label"] for option in options))
    return {"route": "ask_location", "question": question, "location_problem": problem, "location_query": looked_up,
            "location_options": options, "warnings": warnings, "events": add_event(state, f"locate:{problem}")}


def ask_location(state: HealthState) -> dict[str, Any]:
    """Ask where to look, once. The answer can be a place in words, the phone's location
    ({"lat", "lon"}), or one of the options offered."""
    answer = state.get("location_answer")
    question = state.get("question") or LOCATION_QUESTIONS["where"]
    if not answer and state.get("use_interrupt"):
        options = [option["label"] for option in state.get("location_options", [])]
        answer = interrupt({"question": question, "options": options})
    if not (isinstance(answer, dict) or (isinstance(answer, str) and answer.strip())):
        return {"status": "needs_location", "question": question, "route": "stop",
                "events": add_event(state, "ask_location:pending")}
    return {"location_answer": answer, "location_asked": True, "route": "again",
            "events": add_event(state, "ask_location:answered")}


def route_step(state: HealthState) -> str:
    return state.get("route", "stop")


def build_health_graph(checkpointer: Any = None):
    """checkpointer: needed only for real pauses (use_interrupt). A paused request holds health
    details, so it must encrypt what it saves: use utils.checkpoints.make_checkpointer()."""
    if checkpointer is not None and not is_encrypted(checkpointer):
        raise ValueError("The health graph saves personal data; use utils.checkpoints.make_checkpointer()")
    install_safe_client()  # if LangSmith tracing is on, only masked data is sent
    graph = StateGraph(HealthState)
    graph.add_node("validate_request", validate_request)
    graph.add_node("safety_check", safety_check)
    graph.add_node("understand_request", understand_request)
    graph.add_node("emergency", emergency)
    graph.add_node("confirm_emergency", confirm_emergency)
    graph.add_node("ask_clarification", ask_clarification)
    graph.add_node("map_specialty", map_specialty)
    graph.add_node("locate", locate)
    graph.add_node("ask_location", ask_location)

    graph.add_edge(START, "validate_request")
    graph.add_conditional_edges("validate_request", route_step, {"continue": "safety_check", "stop": END})
    graph.add_edge("safety_check", "understand_request")
    after_understanding = {"emergency": "emergency", "confirm_emergency": "confirm_emergency",
                           "find_doctor": "map_specialty", "clarify": "ask_clarification", "stop": END}
    graph.add_conditional_edges("understand_request", route_step, after_understanding)
    graph.add_conditional_edges("confirm_emergency", route_step, {
        "emergency": "emergency", "find_doctor": "map_specialty", "clarify": "ask_clarification", "stop": END,
    })
    graph.add_conditional_edges("ask_clarification", route_step, {"again": "safety_check", "stop": END})
    graph.add_edge("emergency", END)
    graph.add_edge("map_specialty", "locate")
    graph.add_conditional_edges("locate", route_step, {"done": END, "ask_location": "ask_location", "stop": END})
    graph.add_conditional_edges("ask_location", route_step, {"again": "locate", "stop": END})
    return graph.compile(checkpointer=checkpointer)
