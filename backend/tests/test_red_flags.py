"""Tests: emergency red flags found by fixed rules (tools/red_flags.py), and the local specialty
guess used when the AI model can't be (tools/specialties.py). No model, no network."""

import unittest

from backend.tools.red_flags import EMERGENCY_NUMBERS, FLAGS, emergency_message, emergency_numbers, find_red_flags
from backend.tools.specialties import GENERAL_PHYSICIAN, SPECIALTIES, SPECIALTY_WORDS, specialty_from_text

# Each emergency, said the ways a senior or a family member might say or type it.
EMERGENCIES = {
    "chest_pain": [
        "I have chest pain since morning", "Severe pain in my chest and sweating", "chest tightness while walking",
        "I think he is having a heart attack", "Seene mein dard ho raha hai", "chhati me dard hai",
        "papa ko dil ka daura pada", "मुझे सीने में दर्द हो रहा है", "छाती में दर्द",
        "Seene mein bahut dard ho raha hai, pasina aa raha hai",  # missed in the live check before GAP
        "chhati mein bahut tez dard", "सीने में बहुत तेज दर्द",
    ],
    "stroke_signs": [
        "my mother's face is drooping on one side", "his left arm is weak suddenly", "slurred speech since an hour",
        "she can't speak properly", "I think it's a stroke", "lakwa maar gaya", "munh tedha ho gaya",
        "woh bol nahi pa rahe", "दादी को लकवा मार गया", "मुंह टेढ़ा हो गया है",
    ],
    "breathlessness": [
        "I can't breathe", "difficulty breathing at night", "shortness of breath", "he is gasping for air",
        "saans nahi aa rahi", "saans phool rahi hai", "सांस नहीं आ रही", "दम घुट रहा है",
    ],
    "heavy_bleeding": [
        "heavy bleeding from the wound", "the bleeding won't stop", "he is vomiting blood", "coughing up blood",
        "khoon beh raha hai", "bahut khoon nikal raha", "खून बह रहा है", "khoon ruk hi nahi raha",
        "khoon bahut beh raha hai", "खून बहुत बह रहा है",
    ],
    "fainting": [
        "my father fainted in the bathroom", "she passed out", "he is unconscious", "grandma collapsed",
        "not responding when I call him", "behosh ho gaye", "chakkar aa ke gir gaye", "दादाजी बेहोश हो गए",
    ],
    "seizure": [
        "my husband is having a seizure", "she is having fits", "convulsions since five minutes",
        "unko mirgi ka daura pad raha hai", "jhatke aa rahe hain", "दादाजी को मिर्गी का दौरा पड़ा", "झटके आ रहे हैं",
    ],
    "self_harm": [
        "I want to die", "I don't want to live anymore", "I am thinking of suicide", "he wants to kill himself",
        "I want to end my life", "I hurt myself again", "main mar jaana chahta hoon", "ab jeena nahi chahti",
        "khudkushi kar lunga", "मैं आत्महत्या करना चाहता हूँ", "अब जीने का मन नहीं करता",
    ],
    "severe_allergy": [
        "her face is swelling after the injection", "my lips are swollen and itchy", "swollen tongue after medicine",
        "throat is swelling, I think it's anaphylaxis", "severe allergic reaction to the tablet",
        "honth sooj gaye hain", "dawai ke baad gala sooj gaya", "होंठ सूज गए हैं",
    ],
    "fever_confusion": [
        "high fever and he is confused", "fever since yesterday and she is not making sense",
        "fever and she doesn't recognise us", "tez bukhar hai aur behki behki baatein kar rahe hain",
        "bukhar ke saath pehchan nahi rahe", "बुखार है और पहचान नहीं रहे",
    ],
}

# Ordinary doctor requests: no emergency word in them.
ORDINARY = [
    "My knee hurts when I climb stairs", "Mujhe dil ke doctor ko dikhana hai", "I need an eye checkup",
    "my sugar is high, which doctor should I see", "skin rash on my arm since a week", "dentist near Andheri",
    "ghutne mein dard rehta hai", "मुझे आंख के डॉक्टर को दिखाना है", "find a doctor who speaks Marathi",
    "my daughter said see a heart doctor", "cough for two weeks", "remind me to take my BP tablet",
    "fever since two days, which doctor?", "my mother gets confused about her tablets", "dust allergy, sneezing",
    "these new shoes don't fit, my feet hurt", "bukhar hai, doctor ko dikhana hai", "swelling in my knees",
    "khoon ki jaanch karwani hai", "seene ka x-ray karwana hai",  # the wider gap must not catch these
]


class RedFlagTests(unittest.TestCase):
    def test_every_emergency_is_found_in_every_language(self):
        for flag, phrases in EMERGENCIES.items():
            for phrase in phrases:
                with self.subTest(phrase=phrase):
                    self.assertIn(flag, find_red_flags(phrase))

    def test_ordinary_requests_raise_no_flag(self):
        for phrase in ORDINARY:
            with self.subTest(phrase=phrase):
                self.assertEqual(find_red_flags(phrase), [])

    def test_rules_lean_towards_yes(self):
        # A denial still counts: the model and then the person can clear it, the rules never guess.
        self.assertEqual(find_red_flags("no chest pain, just a routine heart checkup"), ["chest_pain"])

    def test_several_flags_in_a_fixed_order_and_case_spacing_ignored(self):
        flags = find_red_flags("He FAINTED   and now has CHEST  PAIN")
        self.assertEqual(flags, ["chest_pain", "fainting"])
        self.assertEqual(find_red_flags("she can’t breathe"), ["breathlessness"])  # curly apostrophe

    def test_empty_or_missing_text(self):
        self.assertEqual(find_red_flags(""), [])
        self.assertEqual(find_red_flags(None), [])

    def test_every_flag_has_test_phrases_and_india_numbers_are_given(self):
        self.assertEqual(set(EMERGENCIES), set(FLAGS))
        self.assertEqual([item["number"] for item in EMERGENCY_NUMBERS], ["112", "108"])

    def test_fever_or_confusion_alone_is_not_an_emergency_together_it_is(self):
        self.assertEqual(find_red_flags("high fever since morning"), [])
        self.assertEqual(find_red_flags("he seems confused today"), [])
        self.assertEqual(find_red_flags("high fever since morning, now he seems confused"), ["fever_confusion"])

    def test_self_harm_adds_the_mental_health_line(self):
        self.assertEqual([n["number"] for n in emergency_numbers(["self_harm"])], ["112", "108", "14416"])
        self.assertIn("Tele-MANAS on 14416", emergency_message(["self_harm"]))
        self.assertEqual([n["number"] for n in emergency_numbers(["chest_pain"])], ["112", "108"])
        self.assertNotIn("14416", emergency_message(["chest_pain"]))


class LocalSpecialtyTests(unittest.TestCase):
    def test_one_named_body_part_or_condition_gives_that_specialty(self):
        cases = {
            "My knee hurts when I climb stairs": "orthopedic",
            "Mujhe dil ke doctor ko dikhana hai": "cardiologist",
            "मुझे आंख के डॉक्टर को दिखाना है": "ophthalmologist",
            "my sugar is high, which doctor should I see": "endocrinologist",
            "skin rash on my arm since a week": "dermatologist",
            "toothache, need a dentist": "dentist",
            "kaan mein awaaz aati hai": "ent",
        }
        for text, specialty in cases.items():
            with self.subTest(text=text):
                self.assertEqual(specialty_from_text(text), specialty)

    def test_several_problems_or_only_a_doctor_wish_give_a_general_physician(self):
        self.assertEqual(specialty_from_text("knee pain and also my eyes are weak"), GENERAL_PHYSICIAN)
        self.assertEqual(specialty_from_text("I want to see a doctor"), GENERAL_PHYSICIAN)
        self.assertEqual(specialty_from_text("doctor ko dikhana hai"), GENERAL_PHYSICIAN)

    def test_not_about_a_doctor_gives_none(self):
        self.assertIsNone(specialty_from_text("what is my pension status"))
        self.assertIsNone(specialty_from_text(""))

    def test_every_specialty_with_words_is_in_the_fixed_list(self):
        self.assertLessEqual(set(SPECIALTY_WORDS), set(SPECIALTIES))
        self.assertIn(GENERAL_PHYSICIAN, SPECIALTIES)


if __name__ == "__main__":
    unittest.main()
