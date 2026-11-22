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
                -> search_doctors  doctors of that specialty near the place, from the stored list
                                (tools/doctor_search.py); none within 20 km: a general physician
        -> ask_clarification    unclear: one question, then the answer is checked from the start
        -> stop                 a task this graph doesn't handle yet
    validate_request -> read_lab_report -> map_specialty -> locate -> ...   (a search from a lab report)

From a lab report (lab-report-doctor-search): the person picks a lab report they have checked and
saved; its results outside the normal range printed on it choose the kind of doctor by a fixed
table (tools/lab_specialty.py), with no model and no network. Very abnormal values make it urgent
("Please see a doctor today"). There are no words, so the emergency rules and the model are not
used, and where to look is the phone's location or the one question (search_near "not_said").

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
anything else is asked (a request may name a place this machine can't recognise). The home saved
in the profile (home_location) is used only when they mean home ("ghar ke paas", or "home" as the
answer to the location question); it is offered in the question, never guessed.

Doctors near the place (step 2d) come live from Practo: every search fetches its area and
specialty through Apify first (tools/doctor_refresh.py, doctor-search Decision 22; Apify learns the
specialty and the area, never the phone's location), and only the doctors that fetch brought are
searched. The stored list is the backup, used and named as such (doctor_source) when the fetch
can't be made or Practo listed nobody near. Details fetched more than 30 days ago are still shown, with a note that they may have changed
(Decision 12). Each doctor found then gets their registration level and a note (step 2e,
tools/doctor_verify.py): verified by Practo, or only listed; not checked by us on the NMC
register. Then a fixed formula orders them and picks the top 3 (step 2f, tools/doctor_rank.py):
only doctors verified by Practo and not rated below 70% can be in the top 3. Last, the choice is
put in plain words (step 2g, tools/doctor_explain.py): fixed sentences filled from the data, no
model; the graph ends with the reasons on each doctor shown and the whole reply in answer.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from ..tools.doctor_explain import explain_doctors
from ..tools.doctor_rank import LOW_RATING_PERCENT, TOP, rank_doctors
from ..tools.doctor_refresh import LIVE, refresh_doctors
from ..tools.provider_import import today_utc
from ..tools.doctor_search import RADII_KM, DoctorListError, find_nearby, load_doctors
from ..tools.doctor_verify import PRACTO_VERIFIED, registration_level, registration_note
from ..tools.geocode import GeocodeError, device_location, find_place, search_near_from_text
from ..tools.lab_specialty import SEE_TODAY, read_lab_results
from ..tools.red_flags import emergency_message, emergency_numbers, find_red_flags
from ..tools.specialties import GENERAL_PHYSICIAN, SPECIALTIES, specialty_from_text
from ..utils.checkpoints import is_encrypted
from ..utils.tracing import install_safe_client

MAX_QUERY_CHARS = 2000
NO_LAB_RESULTS = "The lab report has no test results"
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
HOME_LABEL = "your home"  # never the address: the label is shown and kept with the result
# An answer to the location question that just means home, when a home is saved. "near my home"
# and "ghar ke paas" are caught by search_near_from_text.
HOME_ANSWER = re.compile(r"(?:(?:near|at|around|close\s+to)\s+)?(?:my\s+|our\s+|the\s+)?(?:home|house)|ghar|घर",
                         re.IGNORECASE)
LOCATION_QUESTIONS = {
    "current_location": "Please share your current location, or tell me the area and city you are in.",
    "home": "Which area is your home in? Please tell me the area and the city.",
    "where": "Which area should I look for a doctor in? Please tell me the area and the city.",
    "where_or_here": "Should I look near where you are now? Say \"near me\", or tell me the area and the city.",
    "where_or_home": "Should I look near your home? Say \"home\", or tell me the area and the city.",
    "where_here_or_home": ("Should I look near where you are now, or near your home? Say \"near me\" or \"home\", "
                           "or tell me the area and the city."),
    "not_found": "I could not find that place. Please tell me the area and the city.",
    "ambiguous": "I found more than one place with that name: {options}. Which one do you mean?",
    "unavailable": "I can't look up places just now. Please share your current location.",
}
LOCATION_NOT_FOUND = "The place could not be found. Please start again with the area and the city."
FAR = RADII_KM[-1]
RELAXED_NOTES = {
    "max_fee": f"No such doctor within your fee limit within {FAR} km in our list, so higher fees are shown.",
    "doctor_gender": f"No doctor of the gender you asked for within {FAR} km in our list, so others are shown.",
}
GENERAL_PHYSICIAN_FIRST = ("No {label} within {km} km in our list. A general physician nearby can see you "
                           "first and refer you to one.")
NO_DOCTORS = "No doctor of this kind within {km} km in our list."
SEARCH_UNAVAILABLE = "Doctor search is not available right now."
OLD_DETAILS = "These details were last checked on {day} and may have changed. Call the clinic before going."
NONE_VERIFIED = ("None of these doctors' registrations is marked verified by Practo. Please check the "
                 "doctor's registration before going.")
NO_TOP_CHOICE = ("None of these doctors has both a registration verified by Practo and at least {percent}% of "
                 "patients recommending them, so none is picked as a first choice.")


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
    home_location: dict[str, float]  # {"lat", "lon"} the home saved in the person's profile
    location_answer: Any  # the answer to the location question: a place in words, or {"lat", "lon"}
    lab_report: dict[str, Any]  # instead of query: a confirmed lab report {document_id, report_date, lab_name, tests}

    # From a lab report
    lab_reading: dict[str, Any]  # tools/lab_specialty.read_lab_results: findings, specialty, see_today, reason

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
    doctors: list[dict[str, Any]]  # each with distance_km (2d), registration_level and _note (2e); after rank
    #   the top first, then the rest best first, each with score, score_parts and held_back (2f)
    top_doctors: list[dict[str, Any]]  # at most 3, best first (2f); those shown gain reasons, labels, flags (2g)
    answer: str  # the whole reply in plain words (2g)
    search_radius_km: int
    relaxed_filters: list[str]  # the person's wishes that had to be dropped: max_fee, doctor_gender
    doctor_fallback: str | None  # general_physician when no doctor of the specialty was near
    doctor_refresh: dict[str, Any]  # the live fetch: status, area, results, cost, doctor_ids
    doctor_source: dict[str, Any]  # where the doctors shown came from: kind (live, saved), reason, checked_on
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
    if state.get("lab_report") is not None:  # a search from a lab report: its results, not words
        tests = state["lab_report"].get("tests") if isinstance(state["lab_report"], dict) else None
        if not isinstance(tests, list) or not tests:
            errors.append(NO_LAB_RESULTS)
        return {"errors": errors, "status": "rejected" if errors else "processing",
                "route": "stop" if errors else "lab_report", "events": add_event(state, "validate_request:lab_report")}
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


def read_lab_report(state: HealthState) -> dict[str, Any]:
    """The kind of doctor from a lab report's results (tools/lab_specialty.py): the fixed table, on
    this machine. Fills what understand_request fills for words, so the rest of the search is the
    same: no preferences, where to look not said, urgent when a value is very abnormal."""
    reading = read_lab_results(state["lab_report"]["tests"])
    return {
        "lab_reading": reading,
        "reading_source": "lab_report",
        "red_flags": [],
        "model_emergency": None,
        "model_signs": [],
        "intent": "find_doctor",
        "specialty": reading["specialty"],
        "urgency": "urgent" if reading["see_today"] else "routine",
        "preferences": {},
        "search_near": "not_said",
        "reason": reading["reason"],
        "route": "find_doctor",
        "events": add_event(state, f"read_lab_report:{reading['specialty']}:{len(reading['findings'])}"
                            + (":see_today" if reading["see_today"] else "")),
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


def _from_answer(state: HealthState, phone: dict[str, float] | None, home: dict[str, float] | None,
                 warnings: list[str]):
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
    if home and (HOME_ANSWER.fullmatch(text.strip(" .!?।")) or search_near_from_text(text) == "home"):
        return {**home, "label": HOME_LABEL, "source": "home"}, "", [], ""
    if state.get("location_problem") == "ambiguous" and state.get("location_query"):
        text = f"{state['location_query']}, {text}"  # "Andheri" + "Mumbai"
    place, problem, options = _look_up(text, phone, warnings)
    return ({**place, "source": "answer"} if place else None), problem, options, text


def locate(state: HealthState) -> dict[str, Any]:
    """Where to look for the doctor (step 2a).

    - "near me" (or nothing said about where) and the phone's location is known: use it.
    - "near my home" and a home is saved in the profile: use it.
    - a place named in the request: look it up; far-apart places with that name are asked about,
      unless one is near the phone.
    - otherwise, or if the lookup fails: ask one question (ask_location), offering "near me"
      and "home" when those are known.
    Without the model only "near me" / "near my home" words count: a request that may name a place
    is asked about, never searched near the phone or home by guess."""
    phone = device_location(state.get("current_location"))
    home = device_location(state.get("home_location"))  # checked and rounded like the phone's
    near = state.get("search_near") or "unknown"
    area = (state.get("preferences") or {}).get("area")
    area = area.strip() if isinstance(area, str) else ""
    warnings = list(state.get("warnings", []))
    place, problem, options, looked_up = None, "", [], ""
    if state.get("location_asked"):
        place, problem, options, looked_up = _from_answer(state, phone, home, warnings)
    elif near == "current_location" and phone:
        place = {**phone, "label": HERE_LABEL, "source": "phone"}
    elif near == "home" and home:
        place = {**home, "label": HOME_LABEL, "source": "home"}
    elif area and near != "unknown":
        looked_up = area
        place, problem, options = _look_up(area, phone, warnings)
        place = {**place, "source": "request"} if place else None
    elif near == "not_said" and phone:
        place = {**phone, "label": HERE_LABEL, "source": "phone"}
    elif near in ("current_location", "home"):
        problem = near
    elif phone:
        problem = "where_here_or_home" if home else "where_or_here"
    else:
        problem = "where_or_home" if home else "where"

    if place:
        return {"search_location": place, "location_options": [], "status": "location_found", "route": "search",
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


def _day(iso: str) -> str:
    """"2026-11-17" -> "17 Nov 2026"."""
    day = date.fromisoformat(iso)
    return f"{day.day} {day:%b %Y}"


def _doctor_list() -> tuple[list[dict[str, Any]], DoctorListError | None]:
    try:
        return load_doctors(), None
    except DoctorListError as exc:
        return [], exc


# Why saved doctors were shown instead of live ones (doctor_source.reason), from the fetch's status;
# any other status (no key, no area name, Practo failed) is unreachable.
SAVED_BECAUSE = {"over_budget": "daily_limit", "fetched": "none_live", "reused": "none_live",
                 "no_consent": "not_asked"}


def doctor_source(refresh: dict[str, Any], live: bool, shown: list[dict[str, Any]]) -> dict[str, Any]:
    """Where the doctors shown came from: live (Practo, just now) or saved (the stored list, with
    the newest date among them and why: daily_limit, none_live, not_asked, unreachable)."""
    if live:
        return {"kind": "live", "reason": None, "checked_on": today_utc().isoformat()}
    dates = sorted(doctor["fetched_on"] for doctor in shown if isinstance(doctor.get("fetched_on"), str))
    return {"kind": "saved", "reason": SAVED_BECAUSE.get(refresh["status"], "unreachable"),
            "checked_on": dates[-1] if dates else None}


def search_doctors(state: HealthState) -> dict[str, Any]:
    """Doctors of the specialty near the place, nearest first (step 2d). They are asked from
    Practo live first (doctor-search Decision 22), and only the doctors that fetch brought are
    searched; if it can't be made or none of them is near, the stored list is searched instead
    and the answer says so (doctor_source). The person's wishes (doctor's gender, highest fee) are
    kept while the search widens; dropped only if nobody is found within 20 km, and then the
    answer says so. No doctor of the specialty within 20 km: the nearest general physicians, who
    can see them first and refer them (Decision 9). Details checked more than 30 days ago are
    shown with a note that they may have changed (Decision 12)."""
    warnings = list(state.get("warnings", []))
    specialty = state.get("specialty") if state.get("specialty") in SPECIALTIES else GENERAL_PHYSICIAN
    preferences = state.get("preferences") or {}
    refresh = {"status": "no_consent", "doctor_ids": []}
    if state.get("medical_cloud_consent"):  # Apify would learn the specialty and the area
        refresh = refresh_doctors(state["search_location"], specialty)
    doctors, list_error = _doctor_list()  # read after the fetch, which saves into it
    events = add_event(state, f"search_doctors:refresh:{refresh['status']}")
    if list_error is not None:
        return {"status": "search_unavailable", "doctors": [], "warnings": [*warnings, SEARCH_UNAVAILABLE],
                "errors": [*state.get("errors", []), str(list_error)], "route": "done", "doctor_refresh": refresh,
                "events": [*events, "search_doctors:no_list"]}
    wishes = {"gender": preferences.get("doctor_gender"), "max_fee": preferences.get("max_fee")}
    place = state["search_location"]
    found, live = {"doctors": []}, False
    live_ids = set(refresh.get("doctor_ids") or []) if refresh["status"] in LIVE else set()
    if live_ids:
        found = find_nearby([d for d in doctors if d["doctor_id"] in live_ids], specialty, place, **wishes)
        live = bool(found["doctors"])
    if not live:
        found = find_nearby(doctors, specialty, place, **wishes)
    fallback = None
    if not found["doctors"] and specialty != GENERAL_PHYSICIAN:
        first = find_nearby(doctors, GENERAL_PHYSICIAN, place, **wishes)
        if first["doctors"]:
            found, fallback = first, GENERAL_PHYSICIAN
            warnings.append(GENERAL_PHYSICIAN_FIRST.format(label=SPECIALTIES[specialty], km=FAR))
    if found["doctors"]:
        warnings.extend(RELAXED_NOTES[name] for name in found["relaxed"])
    else:
        warnings.append(NO_DOCTORS.format(km=FAR))
    stale = sorted(doctor["fetched_on"] for doctor in found["doctors"] if doctor.get("stale"))
    if stale:  # the oldest date shown
        warnings.append(OLD_DETAILS.format(day=_day(stale[0])))
    count = len(found["doctors"])
    source = doctor_source(refresh, live, found["doctors"])
    return {
        "doctors": found["doctors"],
        "search_radius_km": found["radius_km"],
        "relaxed_filters": found["relaxed"] if count else [],
        "doctor_fallback": fallback,
        "doctor_refresh": refresh,
        "doctor_source": source,
        "status": "doctors_found" if count else "no_doctors_nearby",
        "warnings": warnings,
        "route": "verify" if count else "done",
        "events": [*events, f"search_doctors:{count}:{found['radius_km']}km"
                   + (":general_physician" if fallback else "") + f":{source['kind']}"],
    }


def verify(state: HealthState) -> dict[str, Any]:
    """Each doctor's registration as Practo lists it (step 2e, Decision 16): practo_verified or
    listed, with a fixed note that says we did not check it on the NMC register ourselves. Doctors
    with no registration never get here (tools/doctor_search.usable). Ranking (2f) is to keep
    listed doctors out of the top 3."""
    doctors = [{**doctor, "registration_level": registration_level(doctor),
                "registration_note": registration_note(doctor)} for doctor in state["doctors"]]
    verified = sum(doctor["registration_level"] == PRACTO_VERIFIED for doctor in doctors)
    warnings = list(state.get("warnings", []))
    if not verified:
        warnings.append(NONE_VERIFIED)
    return {"doctors": doctors, "warnings": warnings, "route": "done",
            "events": add_event(state, f"verify:{verified}/{len(doctors)}")}


def rank(state: HealthState) -> dict[str, Any]:
    """The doctors in order and the top 3 (step 2f, Decision 17): a fixed formula, no model
    (tools/doctor_rank.py), using the language the person asked for, if any. Only doctors whose
    registration Practo verified and who are not rated below LOW_RATING_PERCENT can be in the top."""
    preferences = state.get("preferences") or {}
    doctors, top = rank_doctors(state["doctors"], state["search_radius_km"], preferences.get("doctor_language"))
    warnings = list(state.get("warnings", []))
    if not top:
        warnings.append(NO_TOP_CHOICE.format(percent=LOW_RATING_PERCENT))
    return {"doctors": doctors, "top_doctors": top, "warnings": warnings, "route": "done",
            "events": add_event(state, f"rank:{len(doctors)}:{len(top)}")}


def explain(state: HealthState) -> dict[str, Any]:
    """The reply in plain words (step 2g, Decision 19): fixed sentences filled from the doctors'
    data and the ranking, no model (tools/doctor_explain.py). The notes are the fixed ones the
    person needs (a vague need, general physician first, wishes dropped, none verified, no first
    choice), never the model's words or the technical warnings; old details are flagged on each
    doctor shown instead."""
    specialty = state.get("specialty") if state.get("specialty") in SPECIALTIES else GENERAL_PHYSICIAN
    fallback = state.get("doctor_fallback") == GENERAL_PHYSICIAN
    top = state.get("top_doctors") or []
    notes = [VAGUE_NEED + "."] if state.get("reason") == VAGUE_NEED else []
    lab = state.get("lab_reading")
    if lab:  # why this kind of doctor, first; and "see a doctor today" when a value is very abnormal
        notes = [lab["reason"], *([SEE_TODAY] if lab["see_today"] else [])]
    if fallback:
        notes.append(GENERAL_PHYSICIAN_FIRST.format(label=SPECIALTIES[specialty], km=FAR))
    notes += [RELAXED_NOTES[name] for name in state.get("relaxed_filters", []) if name in RELAXED_NOTES]
    if NONE_VERIFIED in state.get("warnings", []):
        notes.append(NONE_VERIFIED)
    if not top:
        notes.append(NO_TOP_CHOICE.format(percent=LOW_RATING_PERCENT))
    explained = explain_doctors(state["doctors"], top, SPECIALTIES[GENERAL_PHYSICIAN if fallback else specialty],
                                state["search_location"], notes, state.get("urgency"))
    shown = len(top) or min(TOP, len(state["doctors"]))
    return {**explained, "route": "done", "events": add_event(state, f"explain:{shown}")}


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
    graph.add_node("read_lab_report", read_lab_report)
    graph.add_node("understand_request", understand_request)
    graph.add_node("emergency", emergency)
    graph.add_node("confirm_emergency", confirm_emergency)
    graph.add_node("ask_clarification", ask_clarification)
    graph.add_node("map_specialty", map_specialty)
    graph.add_node("locate", locate)
    graph.add_node("ask_location", ask_location)
    graph.add_node("search_doctors", search_doctors)
    graph.add_node("verify", verify)
    graph.add_node("rank", rank)
    graph.add_node("explain", explain)

    graph.add_edge(START, "validate_request")
    graph.add_conditional_edges("validate_request", route_step, {"continue": "safety_check",
                                                                 "lab_report": "read_lab_report", "stop": END})
    graph.add_edge("read_lab_report", "map_specialty")
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
    graph.add_conditional_edges("locate", route_step, {"search": "search_doctors", "ask_location": "ask_location",
                                                       "stop": END})
    graph.add_conditional_edges("search_doctors", route_step, {"verify": "verify", "done": END})
    graph.add_edge("verify", "rank")
    graph.add_edge("rank", "explain")
    graph.add_edge("explain", END)
    graph.add_conditional_edges("ask_location", route_step, {"again": "locate", "stop": END})
    return graph.compile(checkpointer=checkpointer)
