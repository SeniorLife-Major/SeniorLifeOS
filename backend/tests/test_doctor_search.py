"""Tests: doctors near a place from the stored list (tools/doctor_search.py). No network; the
doctors are made up, placed at known distances from a point in Andheri West."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from backend.tools import doctor_search
from backend.tools.doctor_search import MAX_CANDIDATES, DoctorListError, find_nearby, load_doctors

ANDHERI = {"lat": 19.12, "lon": 72.84}
KM_NORTH = 1 / 111.2  # degrees of latitude in one km


def doctor(number, km, specialty="cardiologist", gender="female", fee=1000, clinic=1):
    """A made-up doctor's clinic, km north of ANDHERI."""
    return {"id": f"practo:{number}:{clinic}:{specialty}", "doctor_id": f"practo:{number}", "name": f"Dr. Demo {number}",
            "gender": gender, "specialty": specialty, "fee": fee,
            "clinic": {"name": f"Demo Clinic {clinic}", "lat": round(ANDHERI["lat"] + km * KM_NORTH, 5),
                       "lon": ANDHERI["lon"]}}


def ids(result):
    return [d["doctor_id"] for d in result["doctors"]]


class FindNearbyTests(unittest.TestCase):
    def test_three_within_5_km_stop_the_search_there_nearest_first(self):
        doctors = [doctor(1, 4.0), doctor(2, 1.0), doctor(3, 2.5), doctor(4, 8.0)]
        result = find_nearby(doctors, "cardiologist", ANDHERI)
        self.assertEqual((ids(result), result["radius_km"], result["relaxed"]),
                         (["practo:2", "practo:3", "practo:1"], 5, []))
        self.assertEqual([d["distance_km"] for d in result["doctors"]], [1.0, 2.5, 4.0])

    def test_the_search_widens_to_10_then_20_km(self):
        result = find_nearby([doctor(1, 2), doctor(2, 4), doctor(3, 8)], "cardiologist", ANDHERI)
        self.assertEqual((len(result["doctors"]), result["radius_km"]), (3, 10))
        result = find_nearby([doctor(1, 15)], "cardiologist", ANDHERI)  # fewer than 3 even at 20 km
        self.assertEqual((ids(result), result["radius_km"]), (["practo:1"], 20))
        result = find_nearby([doctor(1, 25)], "cardiologist", ANDHERI)
        self.assertEqual(result["doctors"], [])

    def test_only_the_asked_specialty(self):
        result = find_nearby([doctor(1, 1, "dentist"), doctor(2, 1)], "cardiologist", ANDHERI)
        self.assertEqual(ids(result), ["practo:2"])

    def test_each_doctor_once_at_the_nearest_clinic(self):
        # The nearest comes first, so keeping simply the last clinic seen would fail.
        doctors = [doctor(1, 1.5, clinic=11), doctor(1, 4.0, clinic=10), doctor(1, 30, clinic=12)]
        result = find_nearby(doctors, "cardiologist", ANDHERI)
        self.assertEqual([(d["doctor_id"], d["clinic"]["name"]) for d in result["doctors"]],
                         [("practo:1", "Demo Clinic 11")])

    def test_gender_and_fee_wishes_are_kept_while_widening(self):
        doctors = [doctor(1, 1, gender="male"), doctor(2, 1, fee=3000), doctor(3, 12), doctor(4, 1, fee=None)]
        result = find_nearby(doctors, "cardiologist", ANDHERI, gender="female", max_fee=1500)
        # A farther lady doctor within the fee, rather than nearer ones that are not what was asked.
        self.assertEqual((ids(result), result["radius_km"], result["relaxed"]), (["practo:3"], 20, []))
        result = find_nearby(doctors, "cardiologist", ANDHERI, gender="any", max_fee=None)
        # No wish: the three within 5 km are enough, and an unknown fee counts too.
        self.assertEqual((ids(result), result["radius_km"]), (["practo:1", "practo:2", "practo:4"], 5))

    def test_when_nobody_fits_the_fee_goes_first_then_the_gender(self):
        doctors = [doctor(1, 1, fee=3000), doctor(2, 2, gender="male", fee=500)]
        result = find_nearby(doctors, "cardiologist", ANDHERI, gender="female", max_fee=800)
        self.assertEqual((ids(result), result["relaxed"]), (["practo:1"], ["max_fee"]))  # still a lady doctor
        result = find_nearby([doctor(2, 2, gender="male", fee=3000)], "cardiologist", ANDHERI,
                             gender="female", max_fee=800)
        self.assertEqual((ids(result), result["relaxed"]), (["practo:2"], ["max_fee", "doctor_gender"]))
        result = find_nearby([doctor(2, 2, gender="male")], "cardiologist", ANDHERI, gender="female")
        self.assertEqual(result["relaxed"], ["doctor_gender"])

    def test_wishes_that_are_not_real_are_ignored(self):
        for gender, fee in (("any", 0), ("lady", -5), (None, True), ("female ", float("nan"))):
            with self.subTest(gender=gender, fee=fee):
                result = find_nearby([doctor(1, 1, gender="male", fee=None)], "cardiologist", ANDHERI, gender, fee)
                self.assertEqual((ids(result), result["relaxed"]), (["practo:1"], []))

    def test_at_most_max_candidates_are_kept(self):
        doctors = [doctor(n, 1 + n / 100) for n in range(MAX_CANDIDATES + 5)]
        result = find_nearby(doctors, "cardiologist", ANDHERI)
        self.assertEqual(len(result["doctors"]), MAX_CANDIDATES)
        self.assertEqual(result["doctors"][-1]["doctor_id"], f"practo:{MAX_CANDIDATES - 1}")


class LoadDoctorsTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "doctors.json"
        doctor_search._cache.clear()

    def write(self, doctors):
        self.path.write_text(json.dumps({"doctors": doctors}), encoding="utf-8")

    def test_records_the_search_cannot_use_are_left_out(self):
        good = doctor(1, 1)
        bad = [doctor(2, 1, specialty="astrologer"), doctor(3, 1, gender="other"), doctor(4, 1, fee="1500"),
               {**doctor(5, 1), "clinic": {"lat": 51.5, "lon": -0.12}}, {**doctor(6, 1), "clinic": None},
               {**doctor(7, 1), "doctor_id": 7}, "not a record", doctor(8, 1, fee=True)]
        self.write([good, *bad])
        self.assertEqual(load_doctors(self.path), [good])

    def test_a_missing_or_broken_list_is_an_error(self):
        with self.assertRaisesRegex(DoctorListError, "missing"):
            load_doctors(self.path)
        for content in ("not json", '{"doctors": "x"}', "[]"):
            with self.subTest(content=content):
                self.path.write_text(content, encoding="utf-8")
                doctor_search._cache.clear()
                with self.assertRaisesRegex(DoctorListError, "can't be read"):
                    load_doctors(self.path)

    def test_the_list_is_read_again_only_when_it_changes(self):
        self.write([doctor(1, 1)])
        first = load_doctors(self.path)
        self.assertIs(load_doctors(self.path), first)  # unchanged: not read again
        self.write([doctor(1, 1), doctor(2, 1)])
        stamp = time.time() + 5
        os.utime(self.path, (stamp, stamp))
        self.assertEqual(len(load_doctors(self.path)), 2)


if __name__ == "__main__":
    unittest.main()
