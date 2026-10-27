"""Checks for the scoring and leak-finding helpers of run_complete_suite.py, so the suite's
numbers can be trusted (a scorer that always says 100 would pass everything)."""

import unittest

import run_complete_suite as suite


class SectionScoreTests(unittest.TestCase):
    def test_score_counts_passed_out_of_total_and_lists_failures(self):
        section = suite.Section("privacy")
        section.check(True, "a")
        section.check(False, "b went wrong")
        section.check(True, "c")
        section.check(False, "d went wrong")
        result = section.result()
        self.assertEqual((result["passed"], result["total"], result["score"]), (2, 4, 50.0))
        self.assertEqual(result["failures"], ["b went wrong", "d went wrong"])
        self.assertIn("masking", result["meaning"])

    def test_part_with_no_checks_has_no_score_not_full_marks(self):
        self.assertIsNone(suite.Section("privacy").result()["score"])

    def test_part_not_run_has_no_score_and_keeps_its_reason(self):
        section = suite.Section("storage_api")
        section.check(True, "x")
        result = section.skip("TEST_DATABASE_URL is not set").result()
        self.assertIsNone(result["score"])
        self.assertEqual((result["status"], result["reason"]), ("not_run", "TEST_DATABASE_URL is not set"))


class LeakTests(unittest.TestCase):
    AADHAAR = "2345 6789 0124"

    def test_every_common_spelling_of_an_aadhaar_counts_as_a_leak(self):
        for blob in ("UID 2345 6789 0124", "234567890124", "2345-6789-0124", '{"a": "234567890124"}'):
            self.assertEqual(suite.leaks(blob, [self.AADHAAR]), [self.AADHAAR], blob)

    def test_masked_or_partial_number_is_not_a_leak(self):
        for blob in ("XXXX XXXX 0124", "ending 0124", "2345 6789", "23456789012"):
            self.assertEqual(suite.leaks(blob, [self.AADHAAR]), [], blob)

    def test_pan_and_account_numbers(self):
        self.assertEqual(suite.leaks("pan abcde1234f", ["ABCDE1234F"]), [])  # lower case is not how it is shown
        self.assertEqual(suite.leaks("PAN ABCDE1234F", ["ABCDE1234F"]), ["ABCDE1234F"])
        self.assertEqual(suite.leaks("A/c 001122334455", ["001122334455"]), ["001122334455"])
        self.assertEqual(suite.leaks("A/c XXXXXXXX4455", ["001122334455"]), [])

    def test_short_values_are_not_searched(self):
        self.assertEqual(suite.id_forms("123"), set())


class OwnerEditTests(unittest.TestCase):
    def test_only_wrong_fields_are_corrected_with_the_printed_value(self):
        entry = {"type": "income_certificate", "fields": {
            "name": {"shown": "Kavita Sharma", "value": "Kavita Sharma"},
            "annual_income": {"shown": "Rs. 1,45,000", "value": 145000},
            "issuing_authority": {"shown": "Tahsildar, Pune", "value": "Tahsildar, Pune"},
        }}
        proposal = {"extracted_fields": {"name": {}, "annual_income": {}},
                    "normalized_fields": {"name": "Kavita Sharma", "annual_income": 14500}}
        edits = suite.owner_edits(entry, proposal)
        self.assertEqual(edits["annual_income"], "Rs. 1,45,000")
        self.assertNotIn("name", edits)


class TypedValueTests(unittest.TestCase):
    def test_owner_types_what_is_printed_but_not_in_capitals(self):
        self.assertEqual(suite.typed_value({"shown": "DEMO BANK LTD.", "value": "Demo Bank Ltd."}), "Demo Bank Ltd.")
        self.assertEqual(suite.typed_value({"shown": "Rs. 1,45,000", "value": 145000}), "Rs. 1,45,000")
        self.assertEqual(suite.typed_value({"shown": "10/07/2025", "value": "2025-07-10"}), "10/07/2025")
        rows = [{"name": "Metformin"}]
        self.assertIs(suite.typed_value({"shown": "", "value": rows}), rows)


class SquashTests(unittest.TestCase):
    def test_case_and_spacing_are_ignored(self):
        self.assertEqual(suite.squash("  Kavita\n  SHARMA "), "kavita sharma")


if __name__ == "__main__":
    unittest.main()
