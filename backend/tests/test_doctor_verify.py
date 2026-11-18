"""Tests: each doctor's registration as Practo lists it (tools/doctor_verify.py). No network; the
doctors are made up (test_doctor_search.doctor)."""

import unittest

from backend.tools.doctor_verify import (
    DENTAL_REGISTER, LISTED, NO_REGISTRATION_NOTE, PRACTO_VERIFIED, registration_level, registration_note,
)
from test_doctor_search import REGISTERED, doctor

DENTAL_COUNCIL = "Maharashtra State Dental Council"


def registered(specialty="cardiologist", **changes):
    return doctor(1, 1.0, specialty, registration={**REGISTERED, **changes})


class LevelTests(unittest.TestCase):
    def test_a_number_a_council_a_year_and_practos_mark_is_verified(self):
        self.assertEqual(registration_level(doctor(1, 1.0)), PRACTO_VERIFIED)

    def test_without_practos_mark_or_a_year_it_is_only_listed(self):
        for changes in ({"practo_verified": False}, {"practo_verified": "yes"}, {"practo_verified": None},
                        {"year": None}, {"year": "2004"}, {"year": True}):
            with self.subTest(changes=changes):
                self.assertEqual(registration_level(registered(**changes)), LISTED)

    def test_no_number_or_no_council_is_no_registration(self):
        without = {key: value for key, value in doctor(1, 1.0).items() if key != "registration"}
        cases = {"no number": registered(number=None), "blank number": registered(number="   "),
                 "number not text": registered(number=2004), "no council": registered(council=None),
                 "blank council": registered(council=""), "registration missing": without,
                 "registration empty": doctor(1, 1.0, registration=None),
                 "registration not a record": doctor(1, 1.0, registration="2004/03/1234"),
                 "not a record": "not a record"}
        for why, record in cases.items():
            with self.subTest(why):
                self.assertIsNone(registration_level(record))


class NoteTests(unittest.TestCase):
    def test_a_verified_registration_names_the_council_and_says_we_did_not_check_it(self):
        self.assertEqual(registration_note(doctor(1, 1.0)),
                         "Registration 2004, Maharashtra Medical Council, verified by Practo. "
                         "Not checked by us on the NMC register.")

    def test_a_listed_registration_says_practo_has_not_marked_it_verified(self):
        self.assertEqual(registration_note(registered(practo_verified=False)),
                         "Registration listed on Practo (Maharashtra Medical Council, 2004), not marked verified "
                         "by Practo. Not checked by us on the NMC register.")
        self.assertEqual(registration_note(registered(year=None, council="  Goa Medical Council ")),
                         "Registration listed on Practo (Goa Medical Council), not marked verified by Practo. "
                         "Not checked by us on the NMC register.")

    def test_a_dentist_is_checked_against_the_dental_councils_register(self):
        for why, record in {"dentist, dental council": registered("dentist", council=DENTAL_COUNCIL),
                            "dental council": registered(council=DENTAL_COUNCIL),
                            "dentist": registered("dentist"),
                            "dentist, only listed": registered("dentist", practo_verified=False)}.items():
            with self.subTest(why):
                self.assertTrue(registration_note(record).endswith(f"Not checked by us on the {DENTAL_REGISTER}."))

    def test_the_registration_number_is_never_in_a_note(self):
        for record in (doctor(1, 1.0), registered(practo_verified=False), registered(year=None)):
            note = registration_note(record)
            self.assertNotIn("1234", note)
            self.assertNotIn("/03/", note)

    def test_no_registration_says_so(self):
        self.assertEqual(registration_note(registered(number=None)), NO_REGISTRATION_NOTE)


if __name__ == "__main__":
    unittest.main()
