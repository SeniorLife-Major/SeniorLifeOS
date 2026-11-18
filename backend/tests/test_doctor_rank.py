"""Tests: ordering the doctors found and picking the top 3 (tools/doctor_rank.py). No model, no
network; the doctors are made up, as find_nearby() returns them (with distance_km)."""

import unittest

from backend.tools.doctor_rank import (
    FEE_BEST, FEE_WORST, LOW_RATING, LOW_RATING_PERCENT, NO_DATA, NOT_VERIFIED, PRIOR_STORIES, WEIGHTS, YEARS_FULL,
    degree_level, rank_doctors,
)
from test_doctor_search import REGISTERED, doctor

CARDIOLOGY = "MBBS, MD - General Medicine, DM - Cardiology"


def found(number, km, percent=95, stories=100, years=20, specialist=15, degrees=CARDIOLOGY, fee=1000,
          specialities=("Cardiologist",), languages=("English",), **fields):
    """A made-up doctor as the search returns them, km from the place."""
    return {**doctor(number, km, fee=fee, **fields), "distance_km": km, "practo_specialities": list(specialities),
            "qualifications": degrees, "experience_years": years, "specialist_years": specialist,
            "languages": list(languages),
            "reviews": {"recommend_percent": percent if stories else None, "patient_stories": stories}}


def ranked(doctors, radius_km=5, language=None):
    return rank_doctors(doctors, radius_km, language)


def ids(doctors):
    return [d["doctor_id"] for d in doctors]


def part(doctor, factor):
    return doctor["score_parts"][factor]


def by_id(doctors):
    return {d["doctor_id"]: d for d in doctors}


class FactorTests(unittest.TestCase):
    def test_the_weights_add_up_to_one(self):
        self.assertAlmostEqual(sum(WEIGHTS.values()), 1.0)

    def test_the_weights_are_the_ones_decided(self):
        # Decision 18: specialty 0.20 -> 0.10, distance and reviews 0.15 -> 0.20.
        self.assertEqual(WEIGHTS, {"specialty": 0.10, "distance": 0.20, "reviews": 0.20, "experience": 0.10,
                                   "accessibility": 0.10, "insurance": 0.10, "language": 0.08, "fee": 0.07,
                                   "seen_before": 0.05})

    def test_the_score_is_the_weighted_sum_of_its_parts(self):
        everyone, _ = ranked([found(1, 1.0), found(2, 3.0, percent=80, fee=2500, specialist=0)])
        for d in everyone:
            total = 100 * sum(p["weight"] * p["score"] for p in d["score_parts"].values())
            self.assertAlmostEqual(d["score"], total, delta=0.1)  # parts and total are each rounded
            self.assertEqual(set(d["score_parts"]), set(WEIGHTS))

    def test_nearer_is_better_within_the_radius_used(self):
        everyone, _ = ranked([found(1, 4.0), found(2, 1.0)])
        self.assertEqual(ids(everyone), ["practo:2", "practo:1"])
        self.assertEqual([part(d, "distance")["score"] for d in everyone], [0.8, 0.2])
        wider, _ = ranked([found(1, 4.0)], radius_km=20)
        self.assertEqual(part(wider[0], "distance")["score"], 0.8)  # 4 km of a 20 km search

    def test_the_asked_specialty_listed_first_counts_more(self):
        everyone, _ = ranked([found(1, 1.0, specialities=("General Physician", "Cardiologist")),
                              found(2, 1.0, specialities=("Interventional Cardiologist", "Cardiologist"))])
        doctors = by_id(everyone)
        self.assertEqual(part(doctors["practo:2"], "specialty"), {"weight": 0.10, "score": 1.0,
                                                                   "value": "Interventional Cardiologist"})
        self.assertEqual(part(doctors["practo:1"], "specialty")["score"], 0.5)

    def test_a_branch_of_the_specialty_listed_first_counts_as_the_specialty(self):
        cases = [("orthopedic", ("Spine Surgeon (Ortho)", "Orthopedist"), 1.0),
                 ("orthopedic", ("Spine Surgeon (Neuro)", "Orthopedist"), 0.5),  # a neurosurgeon's branch
                 ("dentist", ("Implantologist", "Dentist"), 1.0),
                 ("cardiologist", ("Cardiac Surgeon", "Cardiologist"), 0.5),       # another practice: still second
                 ("psychiatrist", ("Sexologist", "Psychiatrist"), 0.5)]
        for specialty, specialities, score in cases:
            with self.subTest(first=specialities[0]):
                everyone, _ = ranked([found(1, 1.0, specialty=specialty, specialities=specialities),
                                      found(2, 1.0, specialty=specialty, specialities=specialities[1:])])
                self.assertEqual(part(by_id(everyone)["practo:1"], "specialty")["score"], score)

    def test_a_few_stories_count_for_less_than_many(self):
        doctors = [found(1, 2.0, percent=100, stories=2), found(2, 2.0, percent=96, stories=500),
                   found(3, 2.0, percent=92), found(4, 2.0, percent=90), found(5, 2.0, percent=88)]
        everyone = by_id(ranked(doctors)[0])
        few, many = part(everyone["practo:1"], "reviews"), part(everyone["practo:2"], "reviews")
        middle = 92  # the median of the five
        self.assertEqual(few["value"], {"recommend_percent": 100, "patient_stories": 2,
                                        "counted_as": round((2 * 100 + PRIOR_STORIES * middle) / (2 + PRIOR_STORIES), 1)})
        self.assertLess(few["score"], many["score"])  # 100% of 2 counts below 96% of 500
        self.assertEqual(many["score"], round(((500 * 96 + PRIOR_STORIES * middle) / 510 - 50) / 50, 3))

    def test_experience_uses_specialist_years_then_overall_years_up_to_25(self):
        everyone = by_id(ranked([found(1, 1.0, years=30, specialist=10), found(2, 1.0, years=20, specialist=0),
                                 found(3, 1.0, years=40, specialist=40)])[0])
        self.assertEqual([part(everyone[f"practo:{n}"], "experience")["value"]["years"] for n in (1, 2, 3)],
                         [10, 20, 40])
        scores = [part(everyone[f"practo:{n}"], "experience")["score"] for n in (1, 2, 3)]
        self.assertEqual(scores, [round(0.6 * 10 / YEARS_FULL + 0.4, 3), round(0.6 * 20 / YEARS_FULL + 0.4, 3), 1.0])

    def test_the_highest_degree_is_read_from_the_qualifications(self):
        cases = {CARDIOLOGY: "super_specialty", "MBBS, DNB - Cardiology": "super_specialty",
                 "MBBS, M.Ch - Urology": "super_specialty", "MBBS, MS - General Surgery, MCh - Neuro Surgery":
                 "super_specialty", "MBBS, DrNB - Nephrology": "super_specialty",
                 "MBBS, DNB - General Medicine": "post_graduate", "MBBS, DNB - Radiation Oncology": "post_graduate",
                 "BDS, MDS - Orthodontics": "post_graduate", "MBBS, MRCP (UK)": "post_graduate",
                 "MBBS, DGO": "diploma", "MBBS, Diploma in Dermatology": "diploma",
                 "MBBS": "basic", "BDS": "basic", "MBBS, DMRD": "diploma",
                 "": None, None: None, "Certificate in Yoga": None}
        for text, level in cases.items():
            with self.subTest(text=text):
                self.assertEqual(degree_level(text), level)

    def test_a_lower_fee_is_better_on_a_fixed_scale(self):
        fees = [200, FEE_BEST, 1750, FEE_WORST, 7000]
        everyone = by_id(ranked([found(n, 1.0, fee=fee) for n, fee in enumerate(fees, 1)])[0])
        self.assertEqual([part(everyone[f"practo:{n}"], "fee")["score"] for n in range(1, 6)], [1.0, 1.0, 0.5, 0.0, 0.0])

    def test_a_language_counts_only_when_the_person_asked_for_one(self):
        doctors = [found(1, 1.0, languages=("English", "Marathi")), found(2, 1.0, languages=("English",)),
                   found(3, 1.0, languages=())]
        everyone = by_id(ranked(doctors, language=" marathi ")[0])
        self.assertEqual([part(everyone[f"practo:{n}"], "language")["score"] for n in (1, 2, 3)], [1.0, 0.5, 0.5])
        self.assertEqual(part(everyone["practo:1"], "language")["value"], {"asked": "marathi", "listed": True})
        for asked in (None, "", "  "):
            with self.subTest(asked=asked):
                for d in ranked(doctors, language=asked)[0]:
                    self.assertEqual((part(d, "language")["score"], part(d, "language")["value"]), (0.5, NO_DATA))

    def test_factors_with_no_data_are_the_same_for_everyone(self):
        for d in ranked([found(1, 1.0), found(2, 2.0, percent=70)])[0]:
            self.assertEqual({f: (part(d, f)["score"], part(d, f)["value"]) for f in ("accessibility", "insurance",
                                                                                      "seen_before")},
                             {"accessibility": (0.5, NO_DATA), "insurance": (0.5, NO_DATA), "seen_before": (0.0, NO_DATA)})

    def test_a_missing_value_takes_the_middle_and_is_never_better_than_a_real_one(self):
        doctors = [found(1, 1.0, fee=1000, percent=98, years=25, degrees=CARDIOLOGY),
                   found(2, 1.0, fee=2000, percent=90, years=15, degrees="MBBS, MD - General Medicine"),
                   found(3, 1.0, fee=3000, percent=80, years=5, degrees="MBBS"),
                   found(4, 1.0, fee=None, stories=0, years=None, specialist=None, degrees=None, specialities=())]
        everyone = by_id(ranked(doctors)[0])
        missing = everyone["practo:4"]
        for factor in ("fee", "reviews", "experience", "specialty"):
            with self.subTest(factor=factor):
                known = sorted(part(everyone[f"practo:{n}"], factor)["score"] for n in (1, 2, 3))
                self.assertEqual(part(missing, factor)["score"], known[1])  # the middle of three
                self.assertEqual(part(missing, factor)["value"], NO_DATA)
        self.assertEqual(part(missing, "fee")["score"], 0.4)  # ₹2,000, not a neutral 0.5

    def test_a_list_where_nobody_has_reviews_scores_them_alike(self):
        everyone, _ = ranked([found(1, 1.0, stories=0), found(2, 2.0, stories=0)])
        self.assertEqual({part(d, "reviews")["score"] for d in everyone}, {0.5})


class TopTests(unittest.TestCase):
    def test_the_top_3_are_the_best_verified_doctors_not_rated_low(self):
        listed = {**REGISTERED, "practo_verified": False}
        doctors = [found(1, 0.5, percent=99, registration=listed),     # best score, only listed
                   found(2, 0.6, percent=LOW_RATING_PERCENT - 1, stories=40),  # rated low
                   found(3, 1.0, percent=LOW_RATING_PERCENT, stories=40),      # just at the bar: allowed
                   found(4, 2.0), found(5, 4.0, stories=0),                    # no stories: not rated low
                   found(6, 4.5)]
        everyone, top = ranked(doctors)
        self.assertEqual(ids(top), ["practo:4", "practo:3", "practo:5"])
        self.assertEqual(everyone[:3], top)  # the top first, then the rest best first
        self.assertEqual(ids(everyone[3:]), ["practo:1", "practo:2", "practo:6"])
        self.assertEqual({d["doctor_id"]: d["held_back"] for d in everyone},
                         {"practo:1": NOT_VERIFIED, "practo:2": LOW_RATING, "practo:3": None,
                          "practo:4": None, "practo:5": None, "practo:6": None})
        self.assertGreater(by_id(everyone)["practo:1"]["score"], top[0]["score"])

    def test_a_low_rating_from_a_single_story_still_holds_a_doctor_back(self):
        everyone, top = ranked([found(1, 0.5, percent=0, stories=1), found(2, 4.0)])
        self.assertEqual((ids(top), by_id(everyone)["practo:1"]["held_back"]), (["practo:2"], LOW_RATING))

    def test_with_fewer_doctors_who_qualify_the_top_is_shorter(self):
        low = {"percent": 50, "stories": 30}
        _, top = ranked([found(1, 1.0), found(2, 2.0, **low), found(3, 3.0, **low)])
        self.assertEqual(ids(top), ["practo:1"])
        _, nobody = ranked([found(2, 2.0, **low)])
        self.assertEqual(nobody, [])

    def test_equal_scores_nearer_first_then_by_id(self):
        everyone, _ = ranked([found(2, 1.0), found(1, 1.0)])
        self.assertEqual(everyone[0]["score"], everyone[1]["score"])
        self.assertEqual(ids(everyone), ["practo:1", "practo:2"])

    def test_nothing_found_ranks_nothing(self):
        self.assertEqual(rank_doctors([], 5), ([], []))

    def test_the_input_is_not_changed(self):
        doctors = [found(1, 1.0), found(2, 2.0)]
        before = [dict(d) for d in doctors]
        rank_doctors(doctors, 5)
        self.assertEqual(doctors, before)


if __name__ == "__main__":
    unittest.main()
