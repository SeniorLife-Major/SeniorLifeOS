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


class IsolatedEnvironmentTests(unittest.TestCase):
    """--live must let the Groq code find .env; offline must not. Only checks which function
    is in place; the real .env is never read here."""

    LIVE_MODULES = ("backend.tools.cloud_ocr", "backend.agents.models")

    def fresh_import_inside(self, live: bool) -> dict:
        """Import the Groq modules for the first time inside the block, as a suite run does."""
        import importlib
        import sys

        import backend.agents
        import backend.tools
        saved = {name: importlib.import_module(name) for name in self.LIVE_MODULES}
        for name in self.LIVE_MODULES:
            del sys.modules[name]
        try:
            with suite.isolated_environment(live):
                return {name: importlib.import_module(name).find_env_file for name in self.LIVE_MODULES}
        finally:
            sys.modules.update(saved)  # the rest of the tests keep the modules they already use
            backend.tools.cloud_ocr = saved["backend.tools.cloud_ocr"]
            backend.agents.models = saved["backend.agents.models"]

    def test_live_mode_gives_the_groq_code_the_real_env_finder(self):
        from backend.utils.env import find_env_file
        for name, finder in self.fresh_import_inside(live=True).items():
            self.assertIs(finder, find_env_file, name)

    def test_offline_mode_still_blocks_the_groq_code(self):
        import importlib
        with suite.isolated_environment(False):
            for name in self.LIVE_MODULES:
                self.assertIsNone(importlib.import_module(name).find_env_file(), name)


class SquashTests(unittest.TestCase):
    def test_case_and_spacing_are_ignored(self):
        self.assertEqual(suite.squash("  Kavita\n  SHARMA "), "kavita sharma")


if __name__ == "__main__":
    unittest.main()
