"""Tests: the reply in plain words (tools/doctor_explain.py). No model, no network; the doctors
are made up and ranked first (test_doctor_rank.found), as the graph does."""

import unittest
from datetime import date

from backend.tools.doctor_explain import (
    FEE_GAP, FEW_REVIEWS, LOWEST_FEE, MOST_RECOMMENDED, NEAREST, NO_EXPERIENCE, NO_FEE, NO_REVIEWS, NOT_VERIFIED_FLAG,
    URGENCY_NOTES, explain_doctors,
)
from backend.tools.doctor_rank import rank_doctors
from test_doctor_rank import CARDIOLOGY, found
from test_doctor_search import REGISTERED, TODAY

HERE = {"lat": 19.12, "lon": 72.84, "label": "your current location", "source": "phone"}
NAMED = {**HERE, "label": "Andheri West, Mumbai, Maharashtra", "source": "request",
         "attribution": "© OpenStreetMap contributors, powered by Geoapify"}
LABEL = "Cardiologist (heart)"


def explained(doctors, place=HERE, notes=(), urgency=None, language=None, radius_km=5):
    everyone, top = rank_doctors(doctors, radius_km, language)
    return explain_doctors(everyone, top, LABEL, place, list(notes), urgency)


def shown(result):
    return {d["doctor_id"]: d for d in result["doctors"] if "reasons" in d}


class ReasonTests(unittest.TestCase):
    def test_each_fact_comes_from_the_data(self):
        result = explained([found(1, 1.0, percent=95, stories=66, years=30, specialist=23, fee=1050)])
        self.assertEqual(shown(result)["practo:1"]["reasons"],
                         ["95% recommend (66 patient stories)", f"23 years as a specialist, 30 in all ({CARDIOLOGY})",
                          "Fee ₹1,050"])

    def test_the_years_say_which_years_the_ranking_used(self):
        for years, specialist, text in ((26, 16, "16 years as a specialist, 26 in all"),
                                        (16, 16, "16 years as a specialist"),
                                        (20, 0, "20 years' experience"), (20, None, "20 years' experience"),
                                        (5, 1, "1 year as a specialist, 5 in all")):
            with self.subTest(years=years, specialist=specialist):
                reasons = shown(explained([found(1, 1.0, years=years, specialist=specialist, degrees=None)]))
                self.assertEqual(reasons["practo:1"]["reasons"][1], text)

    def test_missing_facts_are_said_plainly(self):
        result = explained([found(1, 1.0, stories=0, years=None, specialist=None, degrees=None, fee=None)])
        self.assertEqual(shown(result)["practo:1"]["reasons"], [NO_REVIEWS, NO_EXPERIENCE, NO_FEE])

    def test_one_story_and_one_year_read_right(self):
        result = explained([found(1, 1.0, percent=100, stories=1, years=1, specialist=0, degrees="MBBS")])
        self.assertEqual(shown(result)["practo:1"]["reasons"][:2],
                         ["100% recommend (1 patient story)", "1 year's experience (MBBS)"])

    def test_the_language_is_said_only_when_the_person_asked_for_one(self):
        doctors = [found(1, 1.0, languages=("English", "Marathi")), found(2, 2.0, languages=("English",))]
        asked = shown(explained(doctors, language="Marathi"))
        self.assertEqual([asked[f"practo:{n}"]["reasons"][-1] for n in (1, 2)],
                         ["Speaks Marathi", "Marathi not listed on Practo"])
        not_asked = shown(explained(doctors))
        self.assertEqual(not_asked["practo:1"]["reasons"][-1], "Fee ₹1,000")


class LabelAndFlagTests(unittest.TestCase):
    def test_a_label_goes_to_the_one_doctor_who_clearly_has_it(self):
        doctors = [found(1, 1.0, percent=90, stories=200, fee=1500), found(2, 2.0, percent=99, stories=500, fee=1500),
                   found(3, 3.0, percent=95, stories=100, fee=800)]
        labels = {k: d["labels"] for k, d in shown(explained(doctors)).items()}
        self.assertEqual(labels, {"practo:1": [NEAREST], "practo:2": [MOST_RECOMMENDED], "practo:3": [LOWEST_FEE]})

    def test_most_recommended_counts_how_many_stories_back_the_percent(self):
        # The middle % of all found is 90 (two held back at 60%): 100% of 10 counts as 95.0%,
        # 97% of 500 as 96.9%.
        doctors = [found(1, 1.0, percent=100, stories=FEW_REVIEWS), found(2, 1.0, percent=97, stories=500),
                   found(3, 1.0, percent=90, stories=100), found(4, 2.0, percent=60, stories=50),
                   found(5, 2.0, percent=60, stories=50)]
        labels = {k: d["labels"] for k, d in shown(explained(doctors)).items()}
        self.assertEqual(set(labels), {"practo:1", "practo:2", "practo:3"})
        self.assertEqual(labels["practo:2"], [MOST_RECOMMENDED])
        self.assertEqual(labels["practo:1"], [])

    def test_most_recommended_never_goes_to_a_doctor_flagged_for_few_reviews(self):
        # Found live in Koregaon Park: 100% of 2 counted as 96.2%, above 94% of 600.
        doctors = [found(1, 1.9, percent=100, stories=FEW_REVIEWS - 1), found(2, 1.8, percent=94, stories=600),
                   found(3, 1.9, percent=88, stories=FEW_REVIEWS)]
        labels = {k: d["labels"] for k, d in shown(explained(doctors)).items()}
        self.assertEqual(labels["practo:2"], [NEAREST, MOST_RECOMMENDED])
        self.assertNotIn(MOST_RECOMMENDED, labels["practo:1"])
        # Few stories count like none: next to them, one doctor alone has nothing to be compared with.
        alone = shown(explained([found(1, 1.0, percent=100, stories=2), found(2, 2.0, percent=94, stories=600)]))
        self.assertTrue(all(MOST_RECOMMENDED not in d["labels"] for d in alone.values()))

    def test_no_label_when_the_best_is_shared_or_there_is_nothing_to_compare(self):
        tied = shown(explained([found(1, 1.0, fee=1000), found(2, 1.0, fee=1000)]))
        self.assertEqual([d["labels"] for d in tied.values()], [[], []])
        alone = shown(explained([found(1, 1.0)]))
        self.assertEqual(alone["practo:1"]["labels"], [])
        no_fees = shown(explained([found(1, 1.0, fee=None), found(2, 2.0, fee=900)]))
        self.assertNotIn(LOWEST_FEE, no_fees["practo:2"]["labels"])  # one known fee: nothing to compare

    def test_few_reviews_are_flagged(self):
        result = shown(explained([found(1, 1.0, percent=100, stories=3), found(2, 2.0, stories=10)]))
        self.assertIn("Only 3 patient stories", result["practo:1"]["flags"])
        self.assertEqual(result["practo:2"]["flags"], [])

    def test_a_fee_much_above_the_cheapest_shown_is_flagged_with_its_option(self):
        doctors = [found(1, 1.0, fee=1000), found(2, 2.0, fee=1000 + FEE_GAP), found(3, 3.0, fee=1000 + FEE_GAP - 1)]
        result = explained(doctors)
        order = [d["doctor_id"] for d in result["top_doctors"]]
        cheapest = order.index("practo:1") + 1
        flags = shown(result)
        self.assertEqual(flags["practo:2"]["flags"], [f"Fee ₹{FEE_GAP:,} more than option {cheapest}"])
        self.assertEqual(flags["practo:3"]["flags"], [])

    def test_old_details_are_flagged_with_their_date(self):
        stale = {**found(1, 1.0), "stale": True, "fetched_on": "2026-10-05"}
        result = shown(explained([stale, found(2, 2.0)]))
        self.assertIn("Details from 5 Oct 2026, may have changed", result["practo:1"]["flags"])
        self.assertEqual(result["practo:2"]["flags"], [])


class AnswerTests(unittest.TestCase):
    def test_the_answer_lists_the_top_in_order_and_counts_the_doctors_held_back(self):
        doctors = [found(1, 0.5, percent=60, stories=30), found(2, 0.6, registration={**REGISTERED, "practo_verified": False}),
                   found(3, 1.0), found(4, 2.0), found(5, 3.0), found(6, 4.0, percent=50, stories=20)]
        result = explained(doctors)
        answer = result["answer"]
        self.assertTrue(answer.startswith(f"{LABEL} near your current location: 3 best matches"))
        positions = [answer.index(f"{n}. Dr. Demo {doctor_id}") for n, doctor_id in enumerate((3, 4, 5), 1)]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("2 other doctors found were not picked: fewer than 70% of their patients recommend them.", answer)
        self.assertIn("1 other doctor found was not picked: Practo has not verified their registration.", answer)
        for held_back in ("Dr. Demo 1", "Dr. Demo 2", "Dr. Demo 6"):  # counted, never named
            self.assertNotIn(held_back, answer)
        self.assertEqual(result["doctors"][:3], result["top_doctors"])
        self.assertTrue(all("reasons" not in d for d in result["doctors"][3:]))

    def test_with_nobody_fit_to_be_first_the_best_found_are_shown_with_why(self):
        doctors = [found(1, 1.0, percent=50, stories=30), found(2, 2.0, registration={**REGISTERED, "practo_verified": False}),
                   found(3, 3.0, percent=60, stories=4), found(4, 4.0, percent=40, stories=50)]
        result = explained(doctors)
        self.assertEqual(result["top_doctors"], [])
        self.assertTrue(result["answer"].startswith(f"{LABEL} near your current location: 3 found, none a first choice"))
        flags = {k: d["flags"][0] for k, d in shown(result).items()}
        self.assertEqual(len(flags), 3)
        self.assertEqual(flags["practo:2"], NOT_VERIFIED_FLAG)
        self.assertEqual(flags["practo:1"], "Not a first choice: only 50% recommend (30 patient stories)")
        self.assertNotIn("not picked", result["answer"])  # each one shown says why instead

    def test_the_notes_and_how_soon_are_added(self):
        notes = ["No doctor of the gender you asked for within 20 km in our list, so others are shown."]
        for urgency, line in (*URGENCY_NOTES.items(), ("routine", None), (None, None)):
            with self.subTest(urgency=urgency):
                answer = explained([found(1, 1.0)], notes=notes, urgency=urgency)["answer"]
                self.assertIn(notes[0], answer)
                for other in URGENCY_NOTES.values():
                    self.assertEqual(other in answer, other == line)

    def test_the_sources_name_the_oldest_date_shown_and_the_place_credit_only_for_a_looked_up_place(self):
        doctors = [{**found(1, 1.0), "fetched_on": "2026-11-10"}, {**found(2, 2.0), "fetched_on": "2026-11-03"}]
        named = explained(doctors, place=NAMED)["answer"]
        self.assertIn("Doctor details from Practo, checked on 3 Nov 2026. Call the clinic before going.", named)
        self.assertIn("Place search: © OpenStreetMap contributors, powered by Geoapify", named)
        self.assertIn(" near Andheri West, Mumbai, Maharashtra: ", named)
        self.assertNotIn("Place search", explained(doctors)["answer"])

    def test_each_doctor_shown_has_their_registration_note_hours_and_link(self):
        doctor = {**found(1, 1.0), "registration_note": "Registration 2004, Maharashtra Medical Council, verified by "
                                                         "Practo. Not checked by us on the NMC register.",
                  "profile_url": "https://www.practo.com/mumbai/doctor/dr-demo-1",
                  "clinic": {**found(1, 1.0)["clinic"], "locality": "Andheri West", "hours": "Mon-Sat 10:00-13:00",
                             "address": "101 Demo Road"}}
        answer = explained([doctor])["answer"]
        self.assertIn("1. Dr. Demo 1, Cardiologist: Demo Clinic 1, Andheri West, 1.0 km", answer)
        self.assertIn("   Registration 2004, Maharashtra Medical Council, verified by Practo.", answer)
        self.assertIn("   Open Mon-Sat 10:00-13:00 · 101 Demo Road · Book or read more: "
                      "https://www.practo.com/mumbai/doctor/dr-demo-1", answer)

    def test_no_score_or_registration_number_is_ever_shown(self):
        result = explained([found(1, 1.0), found(2, 2.0, percent=60, stories=30)])
        self.assertNotIn(REGISTERED["number"], result["answer"])
        self.assertNotIn("score", result["answer"].casefold())
        for doctor in shown(result).values():
            self.assertNotIn(str(doctor["score"]), result["answer"])

    def test_practo_text_stays_on_one_line_and_within_limits(self):
        odd = {**found(1, 1.0), "name": "Dr. Demo\n\n2. Fake entry\x00", "qualifications": "MBBS " + "x" * 500}
        answer = explained([odd])["answer"]
        first = answer.splitlines()[2]
        self.assertTrue(first.startswith("1. Dr. Demo 2. Fake entry, Cardiologist"))
        self.assertNotIn("\x00", answer)
        self.assertLess(max(len(line) for line in answer.splitlines()), 260)

    def test_the_heading_counts_in_words_that_read_right(self):
        for doctors, heading in (([found(1, 1.0)], "1 best match"),
                                 ([found(1, 1.0), found(2, 2.0)], "2 best matches"),
                                 ([found(1, 1.0, percent=50, stories=30)], "1 found, none a first choice")):
            with self.subTest(heading=heading):
                self.assertEqual(explained(doctors)["answer"].splitlines()[0],
                                 f"{LABEL} near your current location: {heading}")

    def test_the_dates_shown_are_real_days(self):
        self.assertEqual(TODAY, date(2026, 11, 17))  # the made-up doctors are checked on this day
        answer = explained([found(1, 1.0)])["answer"]
        self.assertIn("checked on 17 Nov 2026", answer)


if __name__ == "__main__":
    unittest.main()
