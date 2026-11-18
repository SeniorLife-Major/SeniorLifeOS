"""Tests: the health request graph (graphs/health_graph.py) and the model call it uses
(agents/llm_agents.understand_health_request).

The model is replaced by a stand-in that records what it was sent, so no network is used.
The rule that matters most: the fixed emergency rules run first, the model can add an emergency
but never clear one, and only the person can say a rule finding is not happening now.
"""

import unittest
from unittest.mock import MagicMock, patch

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from backend.graphs.health_graph import (
    CLARIFYING_QUESTION, GENERAL_PHYSICIAN_FIRST, HERE_LABEL, LOCATION_NOT_FOUND, LOCATION_QUESTIONS, MAX_QUERY_CHARS,
    CALL_QUESTION, NO_DOCTORS, NO_TOP_CHOICE, NONE_VERIFIED, OLD_DETAILS, RELAXED_NOTES, SEARCH_UNAVAILABLE,
    VAGUE_NEED, build_health_graph, map_specialty,
)
from backend.tools.doctor_search import DoctorListError
from backend.tools.geocode import ATTRIBUTION, GeocodeError
from test_doctor_search import REGISTERED, doctor
from backend.tools.specialties import GENERAL_PHYSICIAN, SPECIALTIES
from backend.utils.checkpoints import make_checkpointer

TEST_CHECKPOINT_KEY = bytes(range(32))
PHONE = "9876543210"
IN_ANDHERI = {"lat": 19.12014, "lon": 72.84093}  # the phone's location, before rounding
ROUNDED = {"lat": 19.12, "lon": 72.841}


def no_network(text, near=None):
    raise AssertionError(f"a test looked up {text!r} without a stand-in for the place search")


# Made-up doctors around Andheri, Mumbai (none in Pune). The real list (var/providers) changes
# with every import and is never read by these tests.
FAKE_DOCTORS = [
    doctor(1, 1.0, "cardiologist", "female", 1500), doctor(2, 2.0, "cardiologist", "male", 2000),
    doctor(3, 3.0, "cardiologist", "male", 800), doctor(4, 1.5, "orthopedic", "female", 1000),
    doctor(5, 2.5, "general_physician", "male", 500),
]

def already_fresh(place, specialty, doctors):
    """Stands in for refresh_doctors(): the list already holds a recent search."""
    return {"status": "fresh", "area": None, "results": 0, "cost_usd": 0.0}


# No test may reach Geoapify (a lookup without a FakePlaces stand-in fails loudly), Apify or the
# real list.
_no_place_search = patch("backend.graphs.health_graph.find_place", no_network)
_fake_list = patch("backend.graphs.health_graph.load_doctors", lambda: FAKE_DOCTORS)
_no_fetch = patch("backend.graphs.health_graph.refresh_doctors", already_fresh)


def setUpModule():
    _no_place_search.start()
    _fake_list.start()
    _no_fetch.start()


def tearDownModule():
    _no_fetch.stop()
    _fake_list.stop()
    _no_place_search.stop()


def place(label, lat, lon):
    return {"lat": lat, "lon": lon, "label": label, "attribution": ATTRIBUTION}


ANDHERI_WEST = place("Andheri West, Mumbai, Maharashtra", 19.117, 72.834)
ANDHERI_PUNJAB = place("Andheri, Punjab", 30.27, 75.813)
PUNE = place("Pune, Maharashtra", 18.521, 73.855)


class FakePlaces:
    """Stands in for find_place(): answers set per place text (else not found); records lookups."""

    def __init__(self, answers=None, error=None):
        self.answers, self.error, self.calls = answers or {}, error, []

    def __call__(self, text, near=None):
        self.calls.append((text, near))
        if self.error:
            raise self.error
        return self.answers.get(text, {"status": "not_found"})


def found(where):
    return {"status": "found", "place": where}


class FakeStructuredModel:
    """Stands in for structured_model(): records prompts, returns a set answer (or raises)."""

    def __init__(self, answer):
        self.answer = answer  # dict of field -> value, or an Exception to raise
        self.prompts = []

    def __call__(self, schema, method=None):
        agent = MagicMock()

        def invoke(prompt):
            self.prompts.append(prompt)
            if isinstance(self.answer, Exception):
                raise self.answer
            return schema(**self.answer)

        agent.invoke.side_effect = invoke
        return agent


def reading(**overrides):
    """A model answer: by default "find a cardiologist, not an emergency"."""
    answer = {"emergency_now": False, "emergency_reason": "no emergency described", "intent": "find_doctor",
              "specialty": "cardiologist", "urgency": "routine", "reason": "wants a heart doctor"}
    return {**answer, **overrides}


def state(query, **extra):
    return {"actor_id": "senior-meera", "query": query, **extra}


def with_model(query, answer, places=None, **extra):
    """Run the graph with the model allowed (use_llm + medical consent) and a stand-in model
    (and, if given, a stand-in place search)."""
    fake = FakeStructuredModel(answer)
    with patch("backend.agents.llm_agents.structured_model", fake), \
            patch("backend.graphs.health_graph.find_place", places or no_network):
        result = build_health_graph().invoke(state(query, use_llm=True, medical_cloud_consent=True, **extra))
    return result, fake


def without_model(query, places=None, **extra):
    """Run the graph on this machine only (no model), with a stand-in place search if given."""
    with patch("backend.graphs.health_graph.find_place", places or no_network):
        return build_health_graph().invoke(state(query, **extra))


class EmergencyRuleTests(unittest.TestCase):
    def test_rule_finding_stands_without_the_model(self):
        result = build_health_graph().invoke(state("Seene mein dard ho raha hai"))
        self.assertEqual(result["status"], "emergency")
        self.assertEqual(result["emergency"]["source"], "rules")
        self.assertEqual(result["emergency"]["red_flags"], ["chest_pain"])
        self.assertEqual([n["number"] for n in result["emergency"]["numbers"]], ["112", "108"])
        self.assertIsNone(result["model_emergency"])
        self.assertNotIn("specialty_label", result)

    def test_model_agreeing_with_the_rule_is_an_emergency(self):
        result, _ = with_model("chest pain since morning", reading(emergency_now=True))
        self.assertEqual(result["status"], "emergency")
        self.assertEqual(result["emergency"]["source"], "rules+model")

    def test_model_cannot_clear_a_rule_finding_on_its_own(self):
        result, _ = with_model("I had chest pain last year, need a heart doctor", reading(emergency_now=False))
        self.assertEqual(result["status"], "needs_emergency_confirmation")
        self.assertEqual(result["question"], CALL_QUESTION)
        self.assertEqual(result["emergency"]["red_flags"], ["chest_pain"])  # the numbers are shown anyway
        self.assertNotIn("specialty_label", result)

    def test_model_words_the_call_question_in_the_persons_language(self):
        asked = "Kya aap abhi 112 ya 108 par emergency call karna chahte hain?"
        result, fake = with_model("Pichhle saal seene mein dard hua tha, heart doctor chahiye",
                                  reading(emergency_question=asked))
        self.assertEqual(result["question"], asked)
        self.assertEqual([n["number"] for n in result["emergency"]["numbers"]][:2], ["112", "108"])
        self.assertIn("asking whether they want to call emergency services", fake.prompts[0])

    def test_call_question_is_redacted_before_it_is_shown(self):
        result, _ = with_model("I had chest pain last year", reading(
            emergency_question=f"Meera Rao, should I call 112? Or call your son on {PHONE}?"), owner_name="Meera Rao")
        self.assertNotIn(PHONE, result["question"])
        self.assertNotIn("Meera Rao", result["question"])

    def test_only_the_person_clears_it(self):
        query = "I had chest pain last year, need a heart doctor"
        cleared, _ = with_model(query, reading(), emergency_answer={"call_emergency": False})
        self.assertEqual(cleared["status"], "needs_location")  # went on to the doctor search: where to look
        self.assertEqual(cleared["specialty"], "cardiologist")
        self.assertEqual(cleared["emergency_source"], "person")
        for answer in ({"call_emergency": True}, {"call_emergency": "no"}, {"something": 1}, "no"):
            with self.subTest(answer=answer):
                result, _ = with_model(query, reading(), emergency_answer=answer)
                self.assertEqual(result["status"], "emergency")  # anything but a clear "no"
                self.assertEqual(result["emergency"]["source"], "person")

    def test_model_can_add_an_emergency_the_rules_missed(self):
        result, _ = with_model("mere pita ji ki halat bahut kharab hai, kuch bol nahi rahe",
                               reading(emergency_now=True, intent="other", specialty="unsure"))
        self.assertEqual(result["red_flags"], [])
        self.assertEqual(result["status"], "emergency")
        self.assertEqual(result["emergency"]["source"], "model")

    def test_self_harm_shows_the_mental_health_line(self):
        result = build_health_graph().invoke(state("I don't want to live anymore"))
        self.assertEqual(result["status"], "emergency")
        self.assertIn("14416", [n["number"] for n in result["emergency"]["numbers"]])

    def test_model_sign_the_rules_missed_still_picks_the_right_numbers(self):
        result, _ = with_model("sab khatam kar dena chahta hoon, koi faayda nahi",
                               reading(emergency_now=True, emergency_signs=["self_harm"], intent="other",
                                       specialty="unsure"))
        self.assertEqual(result["red_flags"], [])
        self.assertEqual(result["emergency"]["model_signs"], ["self_harm"])
        self.assertIn("14416", [n["number"] for n in result["emergency"]["numbers"]])

    def test_model_failing_still_leaves_the_rules_working(self):
        result, _ = with_model("he fainted just now", RuntimeError("no key"))
        self.assertEqual(result["status"], "emergency")
        self.assertEqual(result["emergency"]["source"], "rules")
        self.assertIn("AI reading unavailable (RuntimeError)", result["warnings"][0])

    def test_answer_to_the_emergency_question_counts_once(self):
        # "Not now" cleared the first finding. The clarifying answer brings a new red flag: asked again.
        result, _ = with_model("I had chest pain last year", reading(intent="unclear", specialty="unsure"),
                               emergency_answer={"call_emergency": False}, clarification_answer="now I can't breathe")
        self.assertEqual(result["red_flags"], ["chest_pain", "breathlessness"])
        self.assertEqual(result["status"], "needs_emergency_confirmation")

    def test_clarifying_answer_is_checked_by_the_rules_too(self):
        result = build_health_graph().invoke(state("please help", clarification_answer="my father fainted"))
        self.assertEqual(result["status"], "emergency")
        self.assertIn("ask_clarification:answered", result["events"])


class UnderstandingTests(unittest.TestCase):
    def test_model_reading_gives_specialty_urgency_and_preferences(self):
        prefs = {"area": "Andheri", "doctor_language": "Marathi", "doctor_gender": "female", "max_fee": 800,
                 "teleconsult_ok": False}
        result, _ = with_model("Mujhe dil ke doctor ko dikhana hai, Andheri mein, lady doctor, 800 tak",
                               reading(urgency="soon", preferences=prefs, search_near="named_place"),
                               places=FakePlaces({"Andheri": found(ANDHERI_WEST)}))
        self.assertEqual(result["search_location"]["label"], ANDHERI_WEST["label"])
        # No lady cardiologist within ₹800: the fee goes first, she stays a lady doctor, and it is said.
        self.assertEqual(result["status"], "doctors_found")
        self.assertEqual([d["doctor_id"] for d in result["doctors"]], ["practo:1"])
        self.assertEqual(result["relaxed_filters"], ["max_fee"])
        self.assertIn(RELAXED_NOTES["max_fee"], result["warnings"])
        self.assertEqual(result["reading_source"], "model")
        self.assertEqual(result["specialty"], "cardiologist")
        self.assertEqual(result["specialty_label"], SPECIALTIES["cardiologist"])
        self.assertEqual(result["urgency"], "soon")
        self.assertEqual(result["preferences"], prefs)

    def test_vague_need_gets_a_general_physician(self):
        result, _ = with_model("I don't feel well these days", reading(specialty="unsure"))
        self.assertEqual(result["specialty"], GENERAL_PHYSICIAN)
        self.assertEqual(result["reason"], VAGUE_NEED)

    def test_map_specialty_itself_only_gives_a_listed_specialty(self):
        # The model and the local words are already limited to the list; this is the last check.
        result = map_specialty({"specialty": "astrologer", "reason": "made up", "events": []})
        self.assertEqual(result["specialty"], GENERAL_PHYSICIAN)
        self.assertEqual(result["reason"], VAGUE_NEED)

    def test_model_answer_outside_the_allowed_values_is_refused(self):
        result, _ = with_model("knee pain when I climb stairs", reading(specialty="astrologer"))
        self.assertEqual(result["reading_source"], "local")  # the bad answer was not used
        self.assertIn("AI reading unavailable (ValidationError)", result["warnings"][0])
        self.assertEqual(result["specialty"], "orthopedic")

    def test_without_medical_consent_the_model_is_not_asked(self):
        fake = FakeStructuredModel(reading())
        with patch("backend.agents.llm_agents.structured_model", fake):
            result = build_health_graph().invoke(state("knee pain when I climb stairs", use_llm=True))
        self.assertEqual(fake.prompts, [])
        self.assertEqual(result["reading_source"], "local")
        self.assertIn("need medical consent", result["warnings"][0])
        self.assertEqual(result["specialty"], "orthopedic")

    def test_prompt_is_redacted_and_names_the_rule_finding(self):
        _, fake = with_model(f"Meera Rao here, call {PHONE}. I had chest pain last year", reading(),
                             owner_name="Meera Rao", emergency_answer={"call_emergency": False})
        prompt = fake.prompts[0]
        self.assertNotIn(PHONE, prompt)
        self.assertNotIn("Meera Rao", prompt)
        self.assertIn("[NAME_1]", prompt)
        self.assertIn("found these emergency signs in the request: chest pain", prompt)

    def test_tasks_not_handled_yet_stop_with_a_message(self):
        result, _ = with_model("remind me to take my BP tablet", reading(intent="medicine", specialty="unsure"))
        self.assertEqual(result["status"], "not_available")
        self.assertIn("Medicines and reminders can't be handled here yet", result["warnings"])


class ClarificationTests(unittest.TestCase):
    def test_unclear_request_asks_one_question(self):
        result = build_health_graph().invoke(state("please help me"))
        self.assertEqual(result["status"], "needs_clarification")
        self.assertEqual(result["question"], CLARIFYING_QUESTION)

    def test_model_question_is_used(self):
        result, _ = with_model("please help me", reading(intent="unclear", specialty="unsure",
                                                         clarifying_question="Is this about a doctor visit?"))
        self.assertEqual(result["question"], "Is this about a doctor visit?")

    def test_answer_is_added_and_read_again(self):
        result = build_health_graph().invoke(state("please help me", clarification_answer="my knee hurts"))
        self.assertEqual(result["status"], "needs_location")  # understood; next, where to look
        self.assertEqual(result["specialty"], "orthopedic")
        self.assertEqual(result["query"], "please help me\nmy knee hurts")

    def test_still_unclear_after_one_answer_stops(self):
        result = build_health_graph().invoke(state("please help me", clarification_answer="I don't know"))
        self.assertEqual(result["status"], "not_understood")
        self.assertEqual(result["events"].count("ask_clarification:answered"), 1)


class LocationTests(unittest.TestCase):
    """Step 2a: the model says where to look; the phone's location is used only when it is meant."""

    def test_near_me_uses_the_phone_location_and_looks_nothing_up(self):
        places = FakePlaces()
        result, _ = with_model("dil ka doctor mere paas", reading(search_near="current_location"), places,
                               current_location=IN_ANDHERI)
        self.assertEqual(result["status"], "doctors_found")
        self.assertEqual(result["search_location"], {**ROUNDED, "label": HERE_LABEL, "source": "phone"})
        self.assertEqual(places.calls, [])
        self.assertIn("locate:phone", result["events"])

    def test_nothing_said_about_where_uses_the_phone(self):
        result, _ = with_model("need a heart doctor", reading(), FakePlaces(), current_location=IN_ANDHERI)
        self.assertEqual(result["search_location"]["source"], "phone")

    def test_a_named_place_is_looked_up_near_the_phone(self):
        places = FakePlaces({"Andheri": found(ANDHERI_WEST)})
        result, _ = with_model("Andheri mein heart doctor", reading(search_near="named_place",
                                                                     preferences={"area": "Andheri"}),
                               places, current_location=IN_ANDHERI)
        self.assertEqual(places.calls, [("Andheri", ROUNDED)])  # the phone's point only steers the search
        self.assertEqual(result["search_location"], {**ANDHERI_WEST, "source": "request"})
        self.assertEqual(result["search_location"]["attribution"], ATTRIBUTION)

    def test_a_place_for_someone_else_wins_over_the_phone(self):
        result, _ = with_model("meri maa ke liye Pune mein eye doctor",
                               reading(specialty="ophthalmologist", search_near="named_place",
                                       preferences={"area": "Pune"}),
                               FakePlaces({"Pune": found(PUNE)}), current_location=IN_ANDHERI)
        self.assertEqual(result["search_location"]["label"], PUNE["label"])

    def test_one_name_for_far_apart_places_is_asked_about(self):
        ambiguous = {"status": "ambiguous", "options": [ANDHERI_PUNJAB, ANDHERI_WEST]}
        named = reading(search_near="named_place", preferences={"area": "Andheri"})
        places = FakePlaces({"Andheri": ambiguous})
        result, _ = with_model("Andheri mein heart doctor", named, places)
        self.assertEqual(result["status"], "needs_location")
        self.assertEqual(result["question"], LOCATION_QUESTIONS["ambiguous"].format(
            options="Andheri, Punjab; Andheri West, Mumbai, Maharashtra"))

        # Picking an offered place uses it as it is: nothing is looked up again.
        places = FakePlaces({"Andheri": ambiguous})
        result, _ = with_model("Andheri mein heart doctor", named, places,
                               location_answer="andheri west, mumbai, maharashtra")
        self.assertEqual(result["search_location"], {**ANDHERI_WEST, "source": "answer"})
        self.assertEqual(len(places.calls), 1)

        # Any other answer narrows the first name down.
        places = FakePlaces({"Andheri": ambiguous, "Andheri, Mumbai": found(ANDHERI_WEST)})
        result, _ = with_model("Andheri mein heart doctor", named, places, location_answer="Mumbai")
        self.assertEqual([text for text, _ in places.calls], ["Andheri", "Andheri, Mumbai"])
        self.assertEqual(result["search_location"]["label"], ANDHERI_WEST["label"])
        self.assertEqual(result["status"], "doctors_found")

    def test_each_kind_of_missing_place_gets_its_own_question(self):
        cases = [("not_said", {}, "where"), ("home", {}, "home"), ("current_location", {}, "current_location"),
                 ("named_place", {}, "where")]  # named, but the model gave no place
        for search_near, prefs, question in cases:
            with self.subTest(search_near=search_near):
                result, _ = with_model("need a heart doctor", reading(search_near=search_near, preferences=prefs),
                                       FakePlaces())
                self.assertEqual(result["status"], "needs_location")
                self.assertEqual(result["question"], LOCATION_QUESTIONS[question])

    def test_the_answer_can_be_a_place_or_the_phone_location(self):
        result, _ = with_model("doctor near me", reading(search_near="current_location"),
                               FakePlaces(), location_answer={"lat": 19.12014, "lon": 72.84093})
        self.assertEqual(result["search_location"], {**ROUNDED, "label": HERE_LABEL, "source": "phone"})
        places = FakePlaces({"Pune": found(PUNE)})
        result, _ = with_model("need a heart doctor", reading(), places, location_answer="Pune")
        self.assertEqual(result["search_location"], {**PUNE, "source": "answer"})

    def test_only_one_location_question(self):
        for answer in ("somewhere I can't name", {"lat": 51.5, "lon": -0.12}, {"lat": "x"}):  # London: not India
            with self.subTest(answer=answer):
                result, _ = with_model("need a heart doctor", reading(), FakePlaces(), location_answer=answer)
                self.assertEqual(result["status"], "location_not_found")
                self.assertIn(LOCATION_NOT_FOUND, result["warnings"])
                self.assertEqual(result["events"].count("ask_location:answered"), 1)

    def test_place_search_down_asks_for_the_phone_location(self):
        places = FakePlaces(error=GeocodeError("Place search needs GEOAPIFY_API_KEY"))
        result, _ = with_model("Andheri mein heart doctor",
                               reading(search_near="named_place", preferences={"area": "Andheri"}), places)
        self.assertEqual(result["status"], "needs_location")
        self.assertEqual(result["question"], LOCATION_QUESTIONS["unavailable"])
        self.assertIn("Place search needs GEOAPIFY_API_KEY; the person is asked instead", result["warnings"])
        result, _ = with_model("Andheri mein heart doctor",
                               reading(search_near="named_place", preferences={"area": "Andheri"}), places,
                               location_answer={"lat": 19.12014, "lon": 72.84093})
        self.assertEqual(result["search_location"]["source"], "phone")

    def test_without_the_model_the_phone_is_used_only_for_near_me_words(self):
        result = without_model("knee doctor near me", current_location=IN_ANDHERI)
        self.assertEqual(result["search_location"]["source"], "phone")
        # The request may name a place this machine can't recognise: asked, never guessed.
        result = without_model("Pune mein ghutne ka doctor", current_location=IN_ANDHERI)
        self.assertEqual(result["status"], "needs_location")
        self.assertEqual(result["question"], LOCATION_QUESTIONS["where_or_here"])
        result = without_model("Pune mein ghutne ka doctor", current_location=IN_ANDHERI, location_answer="yahin")
        self.assertEqual(result["search_location"]["source"], "phone")
        places = FakePlaces({"Pune": found(PUNE)})
        result = without_model("ghutne ka doctor", places, current_location=IN_ANDHERI, location_answer="Pune")
        self.assertEqual(result["search_location"]["label"], PUNE["label"])

    def test_an_emergency_never_looks_for_a_place(self):
        result = without_model("chest pain, need a doctor near me", current_location=IN_ANDHERI)
        self.assertEqual(result["status"], "emergency")
        self.assertFalse([event for event in result["events"] if event.startswith(("locate", "ask_location"))])

    def test_the_model_is_told_how_to_read_where(self):
        _, fake = with_model("need a heart doctor", reading(), FakePlaces(), current_location=IN_ANDHERI)
        self.assertIn("search_near: current_location if they want a doctor near where they are now", fake.prompts[0])
        self.assertIn("exactly as written", fake.prompts[0])


class DoctorSearchTests(unittest.TestCase):
    """Step 2d: doctors near the place, from the stored list (a made-up one here)."""

    def test_doctors_of_the_specialty_near_the_place_are_found(self):
        result, _ = with_model("heart doctor near me", reading(search_near="current_location"), FakePlaces(),
                               current_location=IN_ANDHERI)
        self.assertEqual(result["status"], "doctors_found")
        # The search's nearest-first order is tested in test_doctor_search; the graph ends ranked (2f).
        self.assertEqual([(d["doctor_id"], d["distance_km"]) for d in sorted(result["doctors"], key=lambda d: d["distance_km"])],
                         [("practo:1", 1.0), ("practo:2", 2.0), ("practo:3", 3.0)])
        self.assertEqual(result["search_radius_km"], 5)
        self.assertTrue(all(d["specialty"] == "cardiologist" and "distance_km" in d for d in result["doctors"]))
        self.assertEqual((result["relaxed_filters"], result["doctor_fallback"]), ([], None))
        self.assertIn("search_doctors:3:5km", result["events"])

    def test_without_the_model_near_me_words_still_find_doctors(self):
        result = without_model("knee doctor near me", current_location=IN_ANDHERI)
        self.assertEqual(result["status"], "doctors_found")
        self.assertEqual([d["doctor_id"] for d in result["doctors"]], ["practo:4"])
        self.assertEqual(result["search_radius_km"], 20)  # only one orthopedic: widened to the end

    def test_no_specialist_near_gives_a_general_physician_first(self):
        result, _ = with_model("eye doctor near me", reading(specialty="ophthalmologist",
                                                             search_near="current_location"),
                               FakePlaces(), current_location=IN_ANDHERI)
        self.assertEqual(result["status"], "doctors_found")
        self.assertEqual(result["doctor_fallback"], GENERAL_PHYSICIAN)
        self.assertEqual([d["doctor_id"] for d in result["doctors"]], ["practo:5"])
        self.assertIn(GENERAL_PHYSICIAN_FIRST.format(label=SPECIALTIES["ophthalmologist"], km=20), result["warnings"])
        self.assertIn("search_doctors:1:20km:general_physician", result["events"])
        self.assertEqual((result["events"][-3:], result["doctors"][0]["registration_level"]),
                         (["verify:1/1", "rank:1:1", "explain:1"], "practo_verified"))  # checked too
        self.assertEqual([d["doctor_id"] for d in result["top_doctors"]], ["practo:5"])
        self.assertEqual(result["answer"].splitlines()[0],
                         f"{SPECIALTIES[GENERAL_PHYSICIAN]} near {HERE_LABEL}: 1 best match")  # who is shown
        self.assertIn(GENERAL_PHYSICIAN_FIRST.format(label=SPECIALTIES["ophthalmologist"], km=20), result["answer"])

    def test_nobody_near_says_so(self):
        result, _ = with_model("Pune mein heart doctor", reading(search_near="named_place",
                                                                 preferences={"area": "Pune"}),
                               FakePlaces({"Pune": found(PUNE)}))
        self.assertEqual((result["status"], result["doctors"]), ("no_doctors_nearby", []))
        self.assertIn(NO_DOCTORS.format(km=20), result["warnings"])
        self.assertEqual(result["relaxed_filters"], [])
        self.assertFalse([event for event in result["events"] if event.startswith(("verify", "rank"))])  # nobody

    def test_without_the_list_the_search_is_unavailable(self):
        def no_list():
            raise DoctorListError("The doctor list is missing; build it with tools/provider_import.py")

        with patch("backend.graphs.health_graph.load_doctors", no_list):
            result, _ = with_model("heart doctor near me", reading(search_near="current_location"), FakePlaces(),
                                   current_location=IN_ANDHERI)
        self.assertEqual(result["status"], "search_unavailable")
        self.assertIn(SEARCH_UNAVAILABLE, result["warnings"])
        self.assertFalse([event for event in result["events"] if event.startswith(("verify", "rank"))])

    def test_each_doctor_found_gets_their_registration_level_and_note(self):
        only_listed = {**FAKE_DOCTORS[2], "registration": {**REGISTERED, "practo_verified": False}}
        listed = [*FAKE_DOCTORS[:2], only_listed, *FAKE_DOCTORS[3:]]
        with patch("backend.graphs.health_graph.load_doctors", lambda: listed):
            result, _ = with_model("heart doctor near me", reading(search_near="current_location"), FakePlaces(),
                                   current_location=IN_ANDHERI)
        self.assertEqual(result["status"], "doctors_found")
        self.assertEqual([(d["doctor_id"], d["registration_level"]) for d in result["doctors"]],
                         [("practo:1", "practo_verified"), ("practo:2", "practo_verified"), ("practo:3", "listed")])
        self.assertEqual(result["doctors"][0]["registration_note"],
                         "Registration 2004, Maharashtra Medical Council, verified by Practo. "
                         "Not checked by us on the NMC register.")
        self.assertIn("not marked verified by Practo", result["doctors"][2]["registration_note"])
        self.assertIn("verify:2/3", result["events"])
        self.assertNotIn(NONE_VERIFIED, result["warnings"])

    def test_when_no_doctor_found_is_verified_by_practo_the_answer_says_so(self):
        listed = [{**d, "registration": {**REGISTERED, "practo_verified": False}} for d in FAKE_DOCTORS]
        with patch("backend.graphs.health_graph.load_doctors", lambda: listed):
            result, _ = with_model("heart doctor near me", reading(search_near="current_location"), FakePlaces(),
                                   current_location=IN_ANDHERI)
        self.assertEqual(result["status"], "doctors_found")  # still shown: each note says "not marked verified"
        self.assertIn(NONE_VERIFIED, result["warnings"])
        self.assertIn("verify:0/3", result["events"])
        self.assertIn(NONE_VERIFIED, result["answer"])  # the person is told to check
        self.assertIn(NO_TOP_CHOICE.format(percent=70), result["answer"])

    def test_a_vague_need_is_said_in_the_reply_but_the_models_own_reason_never(self):
        vague, _ = with_model("I need a doctor near me", reading(specialty="unsure", search_near="current_location"),
                              FakePlaces(), current_location=IN_ANDHERI)
        self.assertEqual(vague["specialty"], GENERAL_PHYSICIAN)
        self.assertIn(VAGUE_NEED + ".", vague["answer"])
        heart, _ = with_model("heart doctor near me", reading(search_near="current_location",
                                                              reason="Model says: heart words were used"),
                              FakePlaces(), current_location=IN_ANDHERI)
        self.assertEqual(heart["reason"], "Model says: heart words were used")
        self.assertNotIn("Model says", heart["answer"])
        self.assertNotIn(VAGUE_NEED, heart["answer"])

    def test_the_doctors_found_are_ranked_and_the_top_3_picked(self):
        reviews = {"practo:1": {"recommend_percent": 75, "patient_stories": 40},
                   "practo:2": {"recommend_percent": 99, "patient_stories": 400}}
        reviewed = [{**d, "reviews": reviews.get(d["doctor_id"], {"recommend_percent": None, "patient_stories": 0})}
                    for d in FAKE_DOCTORS]
        with patch("backend.graphs.health_graph.load_doctors", lambda: reviewed):
            result, _ = with_model("heart doctor near me", reading(search_near="current_location"), FakePlaces(),
                                   current_location=IN_ANDHERI)
        # 1 km, 75% of 40, ₹1,500 / 2 km, 99% of 400, ₹2,000 / 3 km, no reviews, ₹800:
        # the reviews lift the second nearest to first.
        self.assertEqual([d["doctor_id"] for d in result["top_doctors"]], ["practo:2", "practo:1", "practo:3"])
        self.assertEqual(result["doctors"], result["top_doctors"])
        self.assertIn("rank:3:3", result["events"])
        first = result["top_doctors"][0]
        self.assertEqual((first["held_back"], first["score_parts"]["fee"]["value"], first["registration_level"]),
                         (None, 2000, "practo_verified"))
        self.assertNotIn(NO_TOP_CHOICE.format(percent=70), result["warnings"])

    def test_the_language_the_person_asked_for_counts_in_the_ranking(self):
        speaks = [{**d, "languages": ["English", "Marathi"] if d["doctor_id"] == "practo:2" else ["English"]}
                  for d in FAKE_DOCTORS]
        with patch("backend.graphs.health_graph.load_doctors", lambda: speaks):
            result, _ = with_model("Marathi bolne wala heart doctor near me",
                                   reading(search_near="current_location", preferences={"doctor_language": "Marathi"}),
                                   FakePlaces(), current_location=IN_ANDHERI)
        self.assertEqual([d["doctor_id"] for d in result["top_doctors"]], ["practo:1", "practo:2", "practo:3"])
        self.assertEqual(result["top_doctors"][1]["score_parts"]["language"]["value"],
                         {"asked": "Marathi", "listed": True})

    def test_when_nobody_qualifies_for_the_top_the_answer_says_so(self):
        low = [{**d, "reviews": {"recommend_percent": 50, "patient_stories": 30}} for d in FAKE_DOCTORS]
        with patch("backend.graphs.health_graph.load_doctors", lambda: low):
            result, _ = with_model("heart doctor near me", reading(search_near="current_location"), FakePlaces(),
                                   current_location=IN_ANDHERI)
        self.assertEqual((result["status"], result["top_doctors"], len(result["doctors"])), ("doctors_found", [], 3))
        self.assertIn(NO_TOP_CHOICE.format(percent=70), result["warnings"])
        self.assertEqual({d["held_back"] for d in result["doctors"]}, {"low_rating"})
        self.assertEqual(result["events"][-2:], ["rank:3:0", "explain:3"])
        self.assertIn("3 found, none a first choice", result["answer"])
        self.assertIn(NO_TOP_CHOICE.format(percent=70), result["answer"])

    def test_the_reply_explains_the_top_3_in_plain_words(self):
        result, _ = with_model("heart doctor near me", reading(search_near="current_location", urgency="urgent"),
                               FakePlaces(), current_location=IN_ANDHERI)
        answer = result["answer"]
        self.assertTrue(answer.startswith(f"{SPECIALTIES['cardiologist']} near {HERE_LABEL}: 3 best matches"))
        for number, doctor in enumerate(result["top_doctors"], 1):
            self.assertIn(f"{number}. {doctor['name']}", answer)
            self.assertEqual(doctor["reasons"][-1], f"Fee ₹{doctor['fee']:,}")
        self.assertIn("Call the clinic today. If it gets worse suddenly, call 108.", answer)  # the model read urgent
        self.assertNotIn(ATTRIBUTION, answer)  # the phone's location: no place was looked up
        self.assertEqual(result["events"][-1], "explain:3")
        no_consent = without_model("heart doctor near me", FakePlaces(), current_location=IN_ANDHERI, use_llm=True)
        self.assertIn("The AI model was not used: health requests need medical consent", no_consent["warnings"])
        self.assertTrue(no_consent["answer"].startswith(f"{SPECIALTIES['cardiologist']} near {HERE_LABEL}: 3 best"))
        self.assertNotIn("AI model", no_consent["answer"])  # technical warnings stay out of the reply

    def test_a_looked_up_place_is_named_and_credited_and_dropped_wishes_are_said(self):
        result, _ = with_model("heart doctor in Andheri West under 500 rupees",
                               reading(search_near="named_place", preferences={"area": "Andheri West", "max_fee": 500}),
                               FakePlaces({"Andheri West": found(ANDHERI_WEST)}))
        answer = result["answer"]
        self.assertIn(f" near {ANDHERI_WEST['label']}: ", answer)
        self.assertIn(f"Place search: {ATTRIBUTION}", answer)
        self.assertEqual(result["relaxed_filters"], ["max_fee"])  # the cheapest cardiologist asks ₹800
        self.assertIn(RELAXED_NOTES["max_fee"], answer)

    def test_details_checked_over_30_days_ago_come_with_a_note(self):
        checked = {"practo:2": "2026-10-10", "practo:3": "2026-10-05"}
        stale = [{**d, "stale": d["doctor_id"] in checked, "fetched_on": checked.get(d["doctor_id"], d["fetched_on"])}
                 for d in FAKE_DOCTORS]
        fresh = [{**d, "stale": False} for d in FAKE_DOCTORS]
        for listed, notes in ((stale, [OLD_DETAILS.format(day="5 Oct 2026")]), (fresh, [])):  # the oldest date
            with self.subTest(notes=notes), patch("backend.graphs.health_graph.load_doctors", lambda: listed):
                result, _ = with_model("heart doctor near me", reading(search_near="current_location"), FakePlaces(),
                                       current_location=IN_ANDHERI)
                self.assertEqual(result["status"], "doctors_found")
                self.assertEqual([w for w in result["warnings"] if "last checked" in w], notes)

    def test_a_list_with_no_current_doctors_makes_the_search_unavailable(self):
        def all_expired():
            raise DoctorListError("The doctor list has no current doctors; refresh it with tools/provider_import.py")

        with patch("backend.graphs.health_graph.load_doctors", all_expired):
            result, _ = with_model("heart doctor near me", reading(search_near="current_location"), FakePlaces(),
                                   current_location=IN_ANDHERI)
        self.assertEqual(result["status"], "search_unavailable")  # not "no doctor nearby": that would be wrong
        self.assertIn("no current doctors", result["errors"][-1])

    def test_with_consent_a_missing_search_is_fetched_first_and_the_list_read_again(self):
        no_cardiologists = [d for d in FAKE_DOCTORS if d["specialty"] != "cardiologist"]
        loader = MagicMock(side_effect=[no_cardiologists, FAKE_DOCTORS])  # before and after the fetch
        fetched = {"status": "fetched", "area": "Andheri West, Mumbai", "results": 3, "cost_usd": 0.0075}
        refresh = MagicMock(return_value=fetched)
        with patch("backend.graphs.health_graph.load_doctors", loader), \
                patch("backend.graphs.health_graph.refresh_doctors", refresh):
            result, _ = with_model("heart doctor near me", reading(search_near="current_location"), FakePlaces(),
                                   current_location=IN_ANDHERI)
        place, specialty, doctors = refresh.call_args.args
        self.assertEqual((place, specialty, doctors),
                         ({**ROUNDED, "label": HERE_LABEL, "source": "phone"}, "cardiologist", no_cardiologists))
        self.assertEqual(result["status"], "doctors_found")
        self.assertEqual(sorted(d["doctor_id"] for d in result["doctors"]), ["practo:1", "practo:2", "practo:3"])
        self.assertEqual(result["doctor_refresh"], fetched)
        self.assertIn("search_doctors:refresh:fetched", result["events"])

    def test_without_consent_nothing_is_fetched(self):
        refresh = MagicMock()
        with patch("backend.graphs.health_graph.refresh_doctors", refresh):
            result = without_model("heart doctor near me", current_location=IN_ANDHERI)
        refresh.assert_not_called()
        self.assertEqual((result["status"], result["doctor_refresh"]), ("doctors_found", {"status": "no_consent"}))

    def test_a_fetch_can_start_the_list_and_a_failed_one_leaves_it_as_it_is(self):
        missing = DoctorListError("The doctor list is missing; build it with tools/provider_import.py")
        for status, lists, want in (("fetched", [missing, FAKE_DOCTORS], "doctors_found"),
                                    ("failed", [missing], "search_unavailable"),
                                    ("over_budget", [FAKE_DOCTORS], "doctors_found")):
            loader = MagicMock(side_effect=lists)
            with self.subTest(status=status), patch("backend.graphs.health_graph.load_doctors", loader), \
                    patch("backend.graphs.health_graph.refresh_doctors",
                          lambda *args, status=status: {"status": status, "area": None, "results": 0, "cost_usd": 0}):
                result, _ = with_model("heart doctor near me", reading(search_near="current_location"),
                                       FakePlaces(), current_location=IN_ANDHERI)
                self.assertEqual(result["status"], want)
                self.assertEqual(loader.call_count, len(lists))  # read again only after a fetch

    def test_the_list_is_read_only_once_a_place_is_found(self):
        loader = MagicMock(return_value=FAKE_DOCTORS)
        with patch("backend.graphs.health_graph.load_doctors", loader):
            for query, extra in (("heart doctor", {}), ("chest pain, heart doctor near me",
                                                         {"current_location": IN_ANDHERI})):
                with self.subTest(query=query):
                    result = without_model(query, **extra)
                    self.assertIn(result["status"], ("needs_location", "emergency"))
        loader.assert_not_called()


class PauseAndValidationTests(unittest.TestCase):
    def test_real_pause_for_the_emergency_question_and_resume(self):
        graph = build_health_graph(checkpointer=make_checkpointer(TEST_CHECKPOINT_KEY))
        config = {"configurable": {"thread_id": "health-1"}}
        fake = FakeStructuredModel(reading())
        with patch("backend.agents.llm_agents.structured_model", fake):
            paused = graph.invoke(state("I had chest pain last year, need a heart doctor", use_llm=True,
                                        medical_cloud_consent=True, use_interrupt=True,
                                        current_location=IN_ANDHERI), config)
            self.assertEqual(paused["__interrupt__"][0].value["question"], CALL_QUESTION)
            self.assertEqual(paused["__interrupt__"][0].value["numbers"][0]["number"], "112")
            result = graph.invoke(Command(resume={"call_emergency": False}), config)
        self.assertEqual(result["search_location"]["source"], "phone")  # nothing said about where
        self.assertEqual(result["status"], "doctors_found")
        self.assertEqual(len(fake.prompts), 1)  # the model is not asked again on resume

    def test_real_pause_for_the_clarifying_question(self):
        graph = build_health_graph(checkpointer=make_checkpointer(TEST_CHECKPOINT_KEY))
        config = {"configurable": {"thread_id": "health-2"}}
        paused = graph.invoke(state("please help me", use_interrupt=True), config)
        self.assertEqual(paused["__interrupt__"][0].value["question"], CLARIFYING_QUESTION)
        result = graph.invoke(Command(resume="eye checkup"), config)
        self.assertEqual(result["specialty"], "ophthalmologist")

    def test_real_pause_for_the_location_question(self):
        graph = build_health_graph(checkpointer=make_checkpointer(TEST_CHECKPOINT_KEY))
        config = {"configurable": {"thread_id": "health-3"}}
        with patch("backend.graphs.health_graph.find_place", FakePlaces({"Pune": found(PUNE)})):
            paused = graph.invoke(state("knee doctor", use_interrupt=True), config)
            self.assertEqual(paused["__interrupt__"][0].value, {"question": LOCATION_QUESTIONS["where"],
                                                                 "options": []})
            result = graph.invoke(Command(resume="Pune"), config)
        self.assertEqual(result["search_location"]["label"], PUNE["label"])
        self.assertEqual(result["status"], "no_doctors_nearby")  # the made-up list has nobody in Pune

    def test_plain_checkpointer_is_refused(self):
        with self.assertRaises(ValueError):
            build_health_graph(checkpointer=InMemorySaver())

    def test_bad_requests_are_rejected_before_anything_reads_them(self):
        for bad in (state(""), state("   "), state("x" * (MAX_QUERY_CHARS + 1)), {"query": "knee pain"},
                    state(None)):
            with self.subTest(bad=str(bad)[:40]):
                result = build_health_graph().invoke(bad)
                self.assertEqual(result["status"], "rejected")
                self.assertEqual(result["events"], ["validate_request"])


if __name__ == "__main__":
    unittest.main()
