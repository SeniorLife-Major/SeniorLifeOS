"""Tests: doctors near a place from the stored list (tools/doctor_search.py). No network; the
doctors are made up, placed at known distances from a point in Andheri West."""

import json
import os
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from backend.tools import doctor_search
from backend.tools.doctor_search import (
    FRESH_DAYS, KEEP_DAYS, MAX_CANDIDATES, DoctorListError, find_nearby, load_doctors,
)

ANDHERI = {"lat": 19.12, "lon": 72.84}
KM_NORTH = 1 / 111.2  # degrees of latitude in one km
TODAY = date(2026, 11, 17)  # the list's "today" in these tests: they never depend on the real date


def doctor(number, km, specialty="cardiologist", gender="female", fee=1000, clinic=1, days_old=0):
    """A made-up doctor's clinic, km north of ANDHERI, fetched days_old days before TODAY."""
    return {"id": f"practo:{number}:{clinic}:{specialty}", "doctor_id": f"practo:{number}", "name": f"Dr. Demo {number}",
            "gender": gender, "specialty": specialty, "fee": fee,
            "fetched_on": (TODAY - timedelta(days=days_old)).isoformat(),
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

    def stored_ids(self):
        return [d["doctor_id"] for d in json.loads(self.path.read_text(encoding="utf-8"))["doctors"]]

    def load(self):
        return load_doctors(self.path, today=TODAY)

    def test_records_the_search_cannot_use_are_left_out(self):
        good = doctor(1, 1)
        bad = [doctor(2, 1, specialty="astrologer"), doctor(3, 1, gender="other"), doctor(4, 1, fee="1500"),
               {**doctor(5, 1), "clinic": {"lat": 51.5, "lon": -0.12}}, {**doctor(6, 1), "clinic": None},
               {**doctor(7, 1), "doctor_id": 7}, "not a record", doctor(8, 1, fee=True)]
        self.write([good, *bad])
        self.assertEqual(self.load(), [{**good, "stale": False}])

    def test_a_missing_or_broken_list_is_an_error(self):
        with self.assertRaisesRegex(DoctorListError, "missing"):
            self.load()
        for content in ("not json", '{"doctors": "x"}', "[]"):
            with self.subTest(content=content):
                self.path.write_text(content, encoding="utf-8")
                doctor_search._cache.clear()
                with self.assertRaisesRegex(DoctorListError, "can't be read"):
                    self.load()

    def test_the_list_is_read_again_only_when_it_changes(self):
        self.write([doctor(1, 1)])
        first = self.load()
        self.assertIs(self.load(), first)  # unchanged: not read again
        self.write([doctor(1, 1), doctor(2, 1)])
        stamp = time.time() + 5
        os.utime(self.path, (stamp, stamp))
        self.assertEqual(len(self.load()), 2)

    def test_details_older_than_30_days_are_marked_and_past_90_days_left_out(self):
        ages = {1: 0, 2: FRESH_DAYS, 3: FRESH_DAYS + 1, 4: KEEP_DAYS, 5: KEEP_DAYS + 1,
                6: -1, 7: -2}  # 6: a day ahead (Apify's clock); 7: two days ahead, a bad date
        self.write([doctor(n, 1, days_old=age) for n, age in ages.items()]
                   + [{**doctor(8, 1), "fetched_on": None}, {**doctor(9, 1), "fetched_on": "17/11/2026"}])
        self.assertEqual([(d["doctor_id"], d["stale"]) for d in self.load()],
                         [("practo:1", False), ("practo:2", False), ("practo:3", True), ("practo:4", True),
                          ("practo:6", False)])

    def test_records_past_90_days_are_deleted_from_the_file(self):
        undated = {**doctor(3, 1), "fetched_on": None}
        self.write([doctor(1, 1, days_old=KEEP_DAYS), doctor(2, 1, days_old=KEEP_DAYS + 1), undated])
        self.assertEqual([d["doctor_id"] for d in self.load()], ["practo:1"])
        self.assertEqual(self.stored_ids(), ["practo:1", "practo:3"])  # undated: age unknown, not deleted
        self.assertEqual([d["doctor_id"] for d in self.load()], ["practo:1"])

    def test_the_next_day_can_make_details_stale(self):
        self.write([doctor(1, 1, days_old=FRESH_DAYS)])
        self.assertFalse(self.load()[0]["stale"])
        self.assertTrue(load_doctors(self.path, today=TODAY + timedelta(days=1))[0]["stale"])

    def test_a_list_with_no_current_doctors_is_an_error(self):
        for doctors in ([], [doctor(1, 1, days_old=KEEP_DAYS + 1)]):
            with self.subTest(doctors=len(doctors)):
                self.write(doctors)
                doctor_search._cache.clear()
                with self.assertRaisesRegex(DoctorListError, "no current doctors"):
                    self.load()
        self.assertEqual(self.stored_ids(), [])  # the expired one was deleted too

    def test_a_clinic_placed_far_from_its_city_is_left_out(self):
        def in_city(record, city, lat=None, lon=None):
            clinic = {**record["clinic"], "city": city, **({"lat": lat, "lon": lon} if lat else {})}
            return {**record, "clinic": clinic}

        mumbai = [in_city(doctor(n, km), "Mumbai") for n, km in ((1, 1), (2, 5), (3, 12))]
        # Seen live: a "Chembur, Mumbai" clinic with coordinates in Bengaluru.
        misplaced = in_city(doctor(4, 1), "Mumbai", 12.91544, 77.61584)
        lone = [in_city(doctor(5, 1), "Nashik", 20.0, 73.79), in_city(doctor(6, 1), "Nashik", 12.9, 77.6)]
        self.write([*mumbai, misplaced, *lone])
        self.assertEqual([d["doctor_id"] for d in self.load()],
                         ["practo:1", "practo:2", "practo:3", "practo:5", "practo:6"])  # 2 Nashik clinics: not checked

    def test_a_failed_delete_still_finds_doctors_and_is_tried_again(self):
        self.write([doctor(1, 1), doctor(2, 1, days_old=KEEP_DAYS + 1)])
        with patch.object(doctor_search, "remove_expired", side_effect=OSError("read-only")):
            self.assertEqual([d["doctor_id"] for d in self.load()], ["practo:1"])
        self.assertEqual(self.stored_ids(), ["practo:1", "practo:2"])
        self.load()  # the next read tries again
        self.assertEqual(self.stored_ids(), ["practo:1"])


if __name__ == "__main__":
    unittest.main()
