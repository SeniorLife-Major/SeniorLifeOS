"""Tests: the sample papers for trying the benefits check on the website (make_benefits_samples.py).

Each made-up person's values, as they are saved after the check step, go through the benefits
graph in screening mode and must give the answers the person should see on "Schemes for me".
The answers are written from the scheme rules (tools/schemes.py), not copied from the graph.
"""

import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

import make_benefits_samples as samples
from backend.graphs.document_graph import MAX_UPLOAD_BYTES
from backend.utils.privacy import is_valid_aadhaar, is_valid_ifsc
from test_benefits_schemes import criterion, record, screen

ONE_PENSION = "You can get only one monthly pension. Choose the one that suits you best."


def papers(key, *, with_extra=False, leave_out=()):
    """A person's papers as confirmed vault records; the extra (someone else's) passbook only when asked."""
    who = samples.person(key)
    return [record(f"{key}-{index}", item["type"], item["values"])
            for index, item in enumerate(who["papers"], start=1)
            if (with_extra or not item.get("extra")) and item["type"] not in leave_out]


def statuses(schemes):
    return {scheme_id: item["status"] for scheme_id, item in schemes.items()}


class SamplePeopleTests(unittest.TestCase):
    def test_ages_on_the_papers(self):
        for key, age in (("kamla", 68), ("ramesh", 75), ("anita", 66), ("lakshmi", 72)):
            _, schemes = screen(papers(key))
            self.assertEqual(criterion(schemes["senior_health_cover"], "age")["actual"], age, key)

    def test_kamla_may_get_the_widow_and_old_age_pensions(self):
        result, schemes = screen(papers("kamla"))
        self.assertEqual(statuses(schemes), {"widow_pension": "likely", "old_age_pension": "likely",
                                             "disability_pension": "add_documents",
                                             "senior_health_cover": "not_eligible"})
        self.assertIn(ONE_PENSION, result["notes"])
        self.assertEqual(criterion(schemes["widow_pension"], "widowed")["status"], "met")
        self.assertEqual(criterion(schemes["old_age_pension"], "poor_household")["status"], "met")

    def test_kamla_without_the_death_certificate_is_asked_for_it(self):
        _, schemes = screen(papers("kamla", leave_out=("death_certificate",)))
        widow = schemes["widow_pension"]
        self.assertEqual(widow["status"], "add_documents")
        self.assertEqual([gap["message"] for gap in widow["gaps"]], ["Add your husband's death certificate."])
        self.assertEqual(schemes["old_age_pension"]["status"], "likely")

    def test_someone_elses_passbook_makes_every_scheme_ask_for_a_check(self):
        _, schemes = screen(papers("kamla", with_extra=True))
        self.assertEqual(statuses(schemes), {scheme_id: "please_check" for scheme_id in schemes})
        self.assertEqual(len(schemes), 4)

    def test_ramesh_may_get_health_cover_and_the_old_age_and_disability_pensions(self):
        result, schemes = screen(papers("ramesh"))
        self.assertEqual(statuses(schemes), {"senior_health_cover": "likely", "old_age_pension": "likely",
                                             "disability_pension": "likely", "widow_pension": "not_eligible"})
        self.assertIn(ONE_PENSION, result["notes"])
        self.assertEqual(criterion(schemes["widow_pension"], "woman")["status"], "unmet")

    def test_anita_with_an_apl_card_gets_none(self):
        result, schemes = screen(papers("anita"))
        self.assertEqual(statuses(schemes), {"senior_health_cover": "not_eligible", "old_age_pension": "not_eligible",
                                             "disability_pension": "not_eligible", "widow_pension": "not_eligible"})
        self.assertEqual(criterion(schemes["old_age_pension"], "poor_household")["status"], "unmet")
        self.assertNotIn(ONE_PENSION, result["notes"])

    def test_lakshmi_is_asked_to_check_her_name_on_the_death_certificate(self):
        result, schemes = screen(papers("lakshmi"))
        self.assertEqual(statuses(schemes), {"widow_pension": "please_check", "old_age_pension": "likely",
                                             "senior_health_cover": "likely", "disability_pension": "add_documents"})
        self.assertEqual(criterion(schemes["widow_pension"], "widowed")["status"], "conflict")
        self.assertIn("spelt a little differently", schemes["widow_pension"]["gaps"][0]["message"])
        self.assertNotIn(ONE_PENSION, result["notes"])  # only one pension is likely


class SamplePagesTests(unittest.TestCase):
    def test_every_page_is_drawn_and_listed_with_its_values(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder)
            answers = samples.generate(out)
            self.assertEqual(json.loads((out / "answers.json").read_text(encoding="utf-8")), answers)
            pages = [(who["folder"], item) for who in answers["people"] for item in who["papers"]]
            self.assertEqual(len(pages), 16)
            for person_folder, item in pages:
                path = out / person_folder / item["file"]
                self.assertLess(path.stat().st_size, MAX_UPLOAD_BYTES, path.name)
                with Image.open(path) as image:
                    self.assertEqual(image.format, "PNG", path.name)

    def test_the_printed_numbers_pass_their_checks(self):
        for who in samples.PEOPLE:
            for item in who["papers"]:
                values = item["values"]
                if "aadhaar_number" in values:
                    self.assertTrue(is_valid_aadhaar(values["aadhaar_number"]), who["key"])
                if "ifsc" in values:
                    self.assertTrue(is_valid_ifsc(values["ifsc"]), who["key"])


if __name__ == "__main__":
    unittest.main()
