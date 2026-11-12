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
    CLARIFYING_QUESTION, MAX_QUERY_CHARS, CALL_QUESTION, VAGUE_NEED, build_health_graph, map_specialty,
)
from backend.tools.specialties import GENERAL_PHYSICIAN, SPECIALTIES
from backend.utils.checkpoints import make_checkpointer

TEST_CHECKPOINT_KEY = bytes(range(32))
PHONE = "9876543210"


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


def with_model(query, answer, **extra):
    """Run the graph with the model allowed (use_llm + medical consent) and a stand-in model."""
    fake = FakeStructuredModel(answer)
    with patch("backend.agents.llm_agents.structured_model", fake):
        result = build_health_graph().invoke(state(query, use_llm=True, medical_cloud_consent=True, **extra))
    return result, fake


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
        self.assertEqual(cleared["status"], "specialty_found")
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
                               reading(urgency="soon", preferences=prefs))
        self.assertEqual(result["status"], "specialty_found")
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
        self.assertEqual(result["status"], "specialty_found")
        self.assertEqual(result["specialty"], "orthopedic")
        self.assertEqual(result["query"], "please help me\nmy knee hurts")

    def test_still_unclear_after_one_answer_stops(self):
        result = build_health_graph().invoke(state("please help me", clarification_answer="I don't know"))
        self.assertEqual(result["status"], "not_understood")
        self.assertEqual(result["events"].count("ask_clarification:answered"), 1)


class PauseAndValidationTests(unittest.TestCase):
    def test_real_pause_for_the_emergency_question_and_resume(self):
        graph = build_health_graph(checkpointer=make_checkpointer(TEST_CHECKPOINT_KEY))
        config = {"configurable": {"thread_id": "health-1"}}
        fake = FakeStructuredModel(reading())
        with patch("backend.agents.llm_agents.structured_model", fake):
            paused = graph.invoke(state("I had chest pain last year, need a heart doctor", use_llm=True,
                                        medical_cloud_consent=True, use_interrupt=True), config)
            self.assertEqual(paused["__interrupt__"][0].value["question"], CALL_QUESTION)
            self.assertEqual(paused["__interrupt__"][0].value["numbers"][0]["number"], "112")
            result = graph.invoke(Command(resume={"call_emergency": False}), config)
        self.assertEqual(result["status"], "specialty_found")
        self.assertEqual(len(fake.prompts), 1)  # the model is not asked again on resume

    def test_real_pause_for_the_clarifying_question(self):
        graph = build_health_graph(checkpointer=make_checkpointer(TEST_CHECKPOINT_KEY))
        config = {"configurable": {"thread_id": "health-2"}}
        paused = graph.invoke(state("please help me", use_interrupt=True), config)
        self.assertEqual(paused["__interrupt__"][0].value["question"], CLARIFYING_QUESTION)
        result = graph.invoke(Command(resume="eye checkup"), config)
        self.assertEqual(result["specialty"], "ophthalmologist")

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
