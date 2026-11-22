"""Tests: the kind of doctor a lab report points to (tools/lab_specialty.py).

The expected specialists come from the fixed table the owner asked for (lab-report-doctor-search
Decision 2); high and low come only from the range printed on the report; the "see a doctor
today" cut-offs are the ones written in VERY_ABNORMAL.
"""

import unittest

from backend.tools.lab_specialty import (
    ALL_NORMAL, GENERAL_ONLY, NONE_COMPARED, ONE_SPECIALIST, SEVERAL, finding_of, plain_unit, read_lab_results,
    rule_for,
)
from backend.tools.specialties import GENERAL_PHYSICIAN, SPECIALTIES


def row(name, value, unit=None, ref_range=None):
    return {"name": name, "value": value, "unit": unit, "ref_range": ref_range}


class TableTests(unittest.TestCase):
    def test_each_kind_of_test_points_to_its_specialist(self):
        cases = {
            "HbA1c": "endocrinologist", "Glycated Haemoglobin": "endocrinologist",
            "Glycosylated Hemoglobin": "endocrinologist",
            "Fasting Blood Sugar (FBS)": "endocrinologist", "Glucose - PP": "endocrinologist",
            "T.S.H": "endocrinologist", "Free T4": "endocrinologist", "Serum Calcium": "endocrinologist",
            "Urine Sugar": "endocrinologist",
            "Total Cholesterol": "cardiologist", "HDL Cholesterol": "cardiologist", "Triglycerides": "cardiologist",
            "LDL": "cardiologist", "Troponin I": "cardiologist", "NT-proBNP": "cardiologist",
            "S. Creatinine": "nephrologist", "eGFR": "nephrologist", "Blood Urea": "nephrologist",
            "Urine Albumin": "nephrologist", "Microalbumin": "nephrologist", "Protein (Urine)": "nephrologist",
            "SGPT (ALT)": "gastroenterologist", "S.G.O.T": "gastroenterologist", "Bilirubin Total": "gastroenterologist",
            "Alkaline Phosphatase": "gastroenterologist", "Albumin": "gastroenterologist",
            "Stool Occult Blood": "gastroenterologist", "HBsAg": "gastroenterologist",
            "PSA (Total)": "urologist",
            "Uric Acid": "rheumatologist", "RA Factor": "rheumatologist", "Anti CCP": "rheumatologist",
            "KOH Mount": "dermatologist",
            "Haemoglobin (Hb)": GENERAL_PHYSICIAN, "Platelet Count": GENERAL_PHYSICIAN,
            "Total Leucocyte Count (TLC)": GENERAL_PHYSICIAN, "ESR": GENERAL_PHYSICIAN, "CRP": GENERAL_PHYSICIAN,
            "Vitamin D (25-OH)": GENERAL_PHYSICIAN, "Vitamin B12": GENERAL_PHYSICIAN, "Sodium (Na+)": GENERAL_PHYSICIAN,
            "Potassium": GENERAL_PHYSICIAN, "Pus Cells": GENERAL_PHYSICIAN, "INR": GENERAL_PHYSICIAN,
        }
        for name, specialty in cases.items():
            with self.subTest(name=name):
                rule, found = rule_for(name)
                self.assertIsNotNone(rule)
                self.assertEqual(found, specialty)

    def test_a_test_the_table_does_not_know_goes_to_a_general_physician(self):
        self.assertEqual(rule_for("Serum Amylase"), (None, GENERAL_PHYSICIAN))
        finding = finding_of(row("Serum Amylase", 180, "U/L", "28 - 100"))
        self.assertEqual((finding["specialty"], finding["known"], finding["flag"]), (GENERAL_PHYSICIAN, False, "high"))

    def test_every_specialist_in_the_table_is_one_doctor_search_knows(self):
        from backend.tools.lab_specialty import TEST_RULES

        self.assertTrue({specialty for _, specialty, _ in TEST_RULES} <= set(SPECIALTIES))


class FlagTests(unittest.TestCase):
    def test_only_the_printed_range_says_high_or_low(self):
        self.assertIsNone(finding_of(row("Fasting Blood Sugar", 92, "mg/dL", "70 - 100")))
        self.assertEqual(finding_of(row("Fasting Blood Sugar", 132, "mg/dL", "70 - 100"))["flag"], "high")
        self.assertEqual(finding_of(row("Haemoglobin", 10.2, "g/dL", "12.0 - 15.5"))["flag"], "low")
        self.assertEqual(finding_of(row("HDL Cholesterol", 35, "mg/dL", "> 40"))["flag"], "low")
        self.assertEqual(finding_of(row("Total Cholesterol", "245", "mg/dL", "< 200"))["flag"], "high")

    def test_a_number_with_no_range_is_not_compared(self):
        self.assertIsNone(finding_of(row("Fasting Blood Sugar", 132, "mg/dL", None)))
        self.assertIsNone(finding_of(row("Total Cholesterol", 245, "mg/dL", "Desirable below 200")))

    def test_words_count_only_when_something_was_found(self):
        self.assertEqual(finding_of(row("Stool Occult Blood", "Positive"))["flag"], "positive")
        self.assertEqual(finding_of(row("HBsAg", "Reactive"))["flag"], "reactive")
        self.assertEqual(finding_of(row("Urine Sugar", "Present"))["specialty"], "endocrinologist")
        for word in ("Negative", "Nil", "Absent", "Non-Reactive", "Normal", "Trace"):
            with self.subTest(word=word):
                self.assertIsNone(finding_of(row("Urine Sugar", word)))

    def test_odd_rows_are_skipped(self):
        for odd in (None, "HbA1c 8", {"value": 9}, row("", 9, "%", "4 - 5.6")):
            with self.subTest(row=odd):
                self.assertIsNone(finding_of(odd))
        self.assertEqual(read_lab_results(None)["specialty"], GENERAL_PHYSICIAN)


class ChoiceTests(unittest.TestCase):
    def test_results_for_one_specialist_go_to_that_specialist(self):
        reading = read_lab_results([row("HbA1c", 8.2, "%", "4.0 - 5.6"), row("Fasting Blood Sugar", 162, "mg/dL", "70 - 100"),
                                    row("Haemoglobin", 13.1, "g/dL", "12.0 - 15.5")])
        self.assertEqual(reading["specialty"], "endocrinologist")
        self.assertEqual(reading["reason"], ONE_SPECIALIST.format(
            article="an", label=SPECIALTIES["endocrinologist"], tests="HbA1c (high), Fasting Blood Sugar (high)"))
        self.assertEqual(([item["test"] for item in reading["findings"]], reading["compared"], reading["unjudged"]),
                         (["HbA1c", "Fasting Blood Sugar"], 3, 0))
        self.assertFalse(reading["see_today"])

    def test_results_for_several_specialists_go_to_a_general_physician(self):
        reading = read_lab_results([row("HbA1c", 8.2, "%", "4.0 - 5.6"), row("LDL Cholesterol", 172, "mg/dL", "< 100")])
        self.assertEqual(reading["specialty"], GENERAL_PHYSICIAN)
        self.assertEqual(reading["specialties"], ["cardiologist", "endocrinologist"])  # in SPECIALTIES order
        self.assertEqual(reading["reason"], SEVERAL.format(
            labels=f"{SPECIALTIES['cardiologist']} and {SPECIALTIES['endocrinologist']}"))

    def test_a_general_physician_result_with_a_specialist_one_is_several_too(self):
        reading = read_lab_results([row("Haemoglobin", 10.1, "g/dL", "12.0 - 15.5"), row("TSH", 7.9, "uIU/mL", "0.4 - 4.0")])
        self.assertEqual((reading["specialty"], reading["specialties"]), (GENERAL_PHYSICIAN,
                                                                           [GENERAL_PHYSICIAN, "endocrinologist"]))

    def test_general_physician_results_only(self):
        reading = read_lab_results([row("Haemoglobin", 10.1, "g/dL", "12.0 - 15.5"), row("Vitamin D", 14, "ng/mL", "30 - 100")])
        self.assertEqual(reading["reason"], GENERAL_ONLY.format(tests="Haemoglobin (low), Vitamin D (low)"))

    def test_all_in_range_goes_to_a_general_physician_and_says_so(self):
        reading = read_lab_results([row("HbA1c", 5.2, "%", "4.0 - 5.6"), row("Urine Sugar", "Nil")])
        self.assertEqual((reading["specialty"], reading["reason"], reading["findings"]),
                         (GENERAL_PHYSICIAN, ALL_NORMAL, []))

    def test_nothing_comparable_says_so(self):
        reading = read_lab_results([row("HbA1c", 8.2, "%"), row("Fasting Blood Sugar", 162, "mg/dL")])
        self.assertEqual((reading["specialty"], reading["reason"]), (GENERAL_PHYSICIAN, NONE_COMPARED))
        self.assertEqual((reading["compared"], reading["unjudged"]), (0, 2))

    def test_kidney_and_liver_and_prostate(self):
        for rows, specialty in (
            ([row("Serum Creatinine", 2.1, "mg/dL", "0.6 - 1.2"), row("eGFR", 38, "mL/min/1.73m2", "> 90")], "nephrologist"),
            ([row("SGPT (ALT)", 112, "U/L", "7 - 56"), row("Bilirubin Total", 2.4, "mg/dL", "0.3 - 1.2")],
             "gastroenterologist"),
            ([row("PSA", 7.8, "ng/mL", "0 - 4")], "urologist"),
            ([row("KOH Mount", "Positive")], "dermatologist"),
        ):
            with self.subTest(specialty=specialty):
                self.assertEqual(read_lab_results(rows)["specialty"], specialty)


class SeeTodayTests(unittest.TestCase):
    def assert_today(self, test_row, flag):
        finding = finding_of(test_row)
        self.assertIsNotNone(finding, test_row)
        self.assertEqual((finding["see_today"], finding["flag"]), (True, flag), test_row)

    def assert_not_today(self, test_row):
        finding = finding_of(test_row)
        self.assertFalse(finding and finding["see_today"], test_row)

    def test_very_abnormal_values_in_known_units(self):
        self.assert_today(row("Random Blood Sugar", 452, "mg/dL", "70 - 140"), "high")
        self.assert_today(row("Fasting Glucose", 2.6, "mmol/L", "3.9 - 5.5"), "low")  # 47 mg/dL
        self.assert_today(row("Potassium", 6.4, "mEq/L", "3.5 - 5.1"), "high")
        self.assert_today(row("Sodium", 121, "mmol/L", "135 - 145"), "low")
        self.assert_today(row("Haemoglobin", 6.2, "g/dL", "12.0 - 15.5"), "low")
        self.assert_today(row("Haemoglobin", 62, "g/L", "120 - 155"), "low")
        self.assert_today(row("Platelet Count", "35,000", "/cumm", "150000 - 450000"), "low")
        self.assert_today(row("Platelet Count", 0.4, "lakh/cumm", "1.5 - 4.5"), "low")
        self.assert_today(row("Total Leucocyte Count", 1.5, "x 10³/µL", "4 - 11"), "low")
        self.assert_today(row("Serum Creatinine", 6.1, "mg/dL", "0.6 - 1.2"), "high")
        self.assert_today(row("Serum Calcium", 13.1, "mg/dL", "8.5 - 10.5"), "high")
        self.assert_today(row("SGPT (ALT)", 1450, "U/L", "7 - 56"), "high")
        self.assert_today(row("INR", 6.2, None, "0.8 - 1.2"), "high")

    def test_troponin_above_its_range_or_found_is_always_today(self):
        self.assert_today(row("Troponin I", 0.9, "ng/mL", "< 0.04"), "high")
        self.assert_today(row("Troponin T", "Positive"), "positive")

    def test_a_very_abnormal_value_counts_without_a_printed_range(self):
        self.assert_today(row("Random Blood Sugar", 480, "mg/dL"), "high")

    def test_out_of_range_but_not_far_is_not_today(self):
        self.assert_not_today(row("Fasting Blood Sugar", 180, "mg/dL", "70 - 100"))
        self.assert_not_today(row("Potassium", 5.4, "mmol/L", "3.5 - 5.1"))
        self.assert_not_today(row("Haemoglobin", 9.8, "g/dL", "12.0 - 15.5"))

    def test_units_it_does_not_know_are_never_guessed(self):
        finding = finding_of(row("Random Blood Sugar", 452, "units", "70 - 140"))
        self.assertEqual((finding["flag"], finding["see_today"]), ("high", False))
        self.assertIsNone(finding_of(row("Random Blood Sugar", 452, "units")))  # no range, no unit: not compared

    def test_the_whole_report_says_today_if_one_result_does(self):
        reading = read_lab_results([row("HbA1c", 11.9, "%", "4.0 - 5.6"), row("Random Blood Sugar", 455, "mg/dL", "70 - 140")])
        self.assertTrue(reading["see_today"])
        self.assertEqual(reading["specialty"], "endocrinologist")

    def test_units_are_written_one_way(self):
        self.assertEqual(plain_unit("x 10³/µL"), "x10^3/ul")
        self.assertEqual(plain_unit("cells/cmm"), "/cumm")
        self.assertEqual(plain_unit("mEq/L"), "meq/l")


if __name__ == "__main__":
    unittest.main()
