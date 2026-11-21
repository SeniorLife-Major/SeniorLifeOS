"""Tests: the papers the benefit schemes need (benefits Part 2): death certificate, ration card,
and the gender on an ID card. Reading, the checks on their values, and masking their numbers."""

import json
import unittest
from unittest.mock import patch

from backend.demo.demo_cases import (
    death_certificate_state,
    kamla_identity_state,
    ration_card_state,
)
from backend.graphs.document_graph import build_document_graph, guess_type_locally
from backend.tools.demo_tools import (
    extract_fields_from_pages,
    normalize_field,
    parse_card_category,
    parse_gender,
)
from backend.tools.document_rules import PROOFS, REQUIRED_FIELDS, match_proofs
from backend.tools.document_type import type_from_text
from backend.utils.privacy import ID_FIELDS, keyed_hash, mask_ids_in_text, redact, shows_identifier
from test_privacy import FAKE_HASH_KEY, hash_key_env


def page(text):
    return [{"page": 1, "quality": 0.95, "text": text}]


def values(fields):
    return {key: item["value"] for key, item in fields.items()}


@patch("backend.utils.privacy.find_env_file", return_value=None)
class GraphTests(unittest.TestCase):
    def run_graph(self, state):
        with hash_key_env(FAKE_HASH_KEY):
            return build_document_graph().invoke(state)

    def test_death_certificate_is_confirmed_with_its_registration_number_masked_and_hashed(self, _):
        result = self.run_graph(death_certificate_state())
        self.assertEqual(result["status"], "confirmed")
        self.assertIn("classify_document:death_certificate", result["events"])
        fields = values(result["vault_record"]["confirmed_fields"])
        self.assertEqual(fields["deceased_name"], "Ramesh Prasad")  # "Late Shri" is not part of the name
        self.assertEqual(fields["spouse_name"], "Kamla Devi")
        self.assertEqual(fields["date_of_death"], "2024-02-14")
        self.assertEqual(fields["age_at_death"], 74)
        self.assertEqual(fields["registration_number"], "XXXXXXXXXXX0012")
        self.assertEqual(result["vault_record"]["identifier_hashes"]["registration_number"],
                         keyed_hash("registration_number", "D-2024:27-90012", FAKE_HASH_KEY))
        self.assertNotIn("D-2024:27-90012", json.dumps(result, default=str))  # page text too

    def test_the_deceased_name_is_never_compared_with_the_owner(self, _):
        # The certificate is about the husband; "name differs from the owner" must not fire.
        result = self.run_graph(death_certificate_state())
        self.assertFalse(any("name differs" in flag for flag in result.get("review_flags", [])))
        self.assertNotIn("name", result["vault_record"]["confirmed_fields"])

    def test_a_date_of_death_after_the_issue_date_goes_to_review(self, _):
        state = death_certificate_state()
        state["document"]["pages"][0]["text"] = state["document"]["pages"][0]["text"].replace(
            "Date of issue: 02/03/2024", "Date of issue: 02/01/2024")
        result = self.run_graph(state)
        self.assertEqual(result["status"], "needs_review")
        self.assertIn("date_of_death is after the issue date", result["review_flags"])

    def test_the_owner_cannot_confirm_dates_out_of_order(self, _):
        state = death_certificate_state()
        state["review_decision"] = {"action": "confirm", "reviewer_id": "senior-kamla",
                                    "edits": {"issue_date": "2024-01-01"}}
        result = self.run_graph(state)
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["errors"], ["Date of death cannot be after the date it was issued"])

    def test_ration_card_is_confirmed_with_its_number_masked(self, _):
        result = self.run_graph(ration_card_state())
        self.assertEqual(result["status"], "confirmed")
        fields = values(result["vault_record"]["confirmed_fields"])
        self.assertEqual(fields["head_of_family"], "Raju Prasad")  # her son: no owner-name flag
        self.assertEqual(fields["card_category"], "PHH")
        self.assertEqual(fields["residence_state"], "MH")
        self.assertEqual(fields["family_members"], "Raju Prasad, Kamla Devi, Sita Prasad")
        self.assertEqual(fields["ration_card_number"], "XXXXXXXX6789")
        self.assertNotIn("272100456789", json.dumps(result, default=str))

    def test_an_unknown_card_category_goes_to_review(self, _):
        result = self.run_graph(ration_card_state(category="Saffron"))
        self.assertEqual(result["status"], "needs_review")
        self.assertIn("card_category: missing or invalid", result["review_flags"])

    def test_aadhaar_gender_printed_with_no_label_is_read_and_reviewed(self, _):
        result = self.run_graph(kamla_identity_state())
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["normalized_fields"]["gender"], "female")
        self.assertIn("gender: found as a word printed with no label, please check", result["review_flags"])

    def test_typed_in_death_certificate_needs_dates_in_order(self, _):
        state = {"actor_id": "senior-kamla", "document_id": "typed-death", "claimed_type": "death_certificate",
                 "document": {"owner_id": "senior-kamla", "as_of": "2026-11-21"},
                 "manual_fields": {"deceased_name": "Ramesh Prasad", "date_of_death": "2024-02-14",
                                   "issue_date": "2024-02-01", "issuing_authority": "Registrar"}}
        result = self.run_graph(state)
        self.assertEqual(result["status"], "rejected")
        self.assertIn("Date of death cannot be after the date it was issued", result["errors"])
        state["manual_fields"]["issue_date"] = "2024-03-01"
        state["manual_fields"]["registration_number"] = "D-2024:27-90012"
        result = self.run_graph(state)
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["vault_record"]["confirmed_fields"]["registration_number"]["value"],
                         "XXXXXXXXXXX0012")


class ReadingTests(unittest.TestCase):
    def test_death_certificate_labels(self):
        found = extract_fields_from_pages(page(
            "Name: Late Ramesh Prasad\nDate of death: 14-02-2024\nName of Husband/Wife: Smt. Kamla Devi\n"
            "Reg. No: D-2024:27-90012\nDate: 02/03/2024"), "death_certificate")
        self.assertEqual(values(found), {"deceased_name": "Late Ramesh Prasad", "date_of_death": "14-02-2024",
                                         "spouse_name": "Smt. Kamla Devi", "registration_number": "D-2024:27-90012",
                                         "issue_date": "02/03/2024"})

    def test_ration_card_labels(self):
        found = extract_fields_from_pages(page(
            "Name: Raju Prasad\nCategory: AAY\nState: Maharashtra\nRC No: 272100456789"), "ration_card")
        self.assertEqual(values(found), {"head_of_family": "Raju Prasad", "card_category": "AAY",
                                         "residence_state": "Maharashtra", "ration_card_number": "272100456789"})

    def test_name_still_means_the_owner_on_other_types(self):
        found = extract_fields_from_pages(page("Name: Kamla Devi\nGender: Female"), "identity")
        self.assertEqual(values(found), {"name": "Kamla Devi", "gender": "Female"})
        found = extract_fields_from_pages(page("Patient Name: Kamla Devi\nAge / Sex: 68 / F"), "lab_report")
        self.assertNotIn("gender", found)  # "Sex" only ends a value on a lab report

    def test_two_gender_words_on_a_card_are_not_guessed(self):
        found = extract_fields_from_pages(page("Name: X\nMALE\nFEMALE"), "identity")
        self.assertNotIn("gender", found)

    def test_values(self):
        self.assertEqual(parse_gender("FEMALE / महिला"), "female")
        self.assertEqual(parse_gender("M"), "male")
        self.assertEqual(parse_gender("Transgender"), "other")
        self.assertIsNone(parse_gender("Male / Female"))
        for printed, code in (("Antyodaya Anna Yojana", "AAY"), ("PHH (NFSA)", "PHH"), ("Priority Household", "PHH"),
                              ("Non Priority Household", "NPHH"), ("Below Poverty Line", "BPL"), ("APL", "APL"),
                              ("अंत्योदय", "AAY")):
            self.assertEqual(parse_card_category(printed), code, printed)
        self.assertIsNone(parse_card_category("Saffron"))
        self.assertEqual(normalize_field("age_at_death", "74 years"), 74)
        self.assertIsNone(normalize_field("age_at_death", "200"))
        self.assertEqual(normalize_field("deceased_name", "Late Shri Ramesh Prasad"), "Ramesh Prasad")
        self.assertEqual(normalize_field("family_members", "Smt. Kamla Devi; Raju"), "Kamla Devi, Raju")
        self.assertIsNone(normalize_field("registration_number", "12"))
        self.assertEqual(normalize_field("ration_card_number", "27 2100 456789"), "272100456789")
        self.assertIsNone(normalize_field("ration_card_number", "ABCDEF"))

    def test_type_from_words_and_file_name(self):
        self.assertEqual(type_from_text("DEATH CERTIFICATE\nName of deceased: X\nDate of death: 1/1/2024"),
                         "death_certificate")
        self.assertEqual(type_from_text("RATION CARD\nPublic Distribution System\nCard type: AAY"), "ration_card")
        self.assertEqual(guess_type_locally("ration_card_page.jpg", ""), "ration_card")
        self.assertEqual(guess_type_locally("husband_death_cert.pdf", ""), "death_certificate")


class MaskingTests(unittest.TestCase):
    def test_the_new_numbers_are_id_fields(self):
        self.assertIn("registration_number", ID_FIELDS)
        self.assertIn("ration_card_number", ID_FIELDS)

    def test_a_ration_card_number_after_its_label_is_hidden_everywhere(self):
        self.assertEqual(mask_ids_in_text("Ration Card No: 272100456789"), "Ration Card No: XXXXXXXX6789")
        self.assertEqual(redact("RC No. MH-27-0012345")[0], "RC No. [RATION_CARD_1]")
        self.assertTrue(shows_identifier("Ration card number: 272100456789", generic_codes=False))


class ProofTests(unittest.TestCase):
    def test_new_proofs_use_required_fields(self):
        for proof in ("proof_of_widowhood", "proof_of_bpl"):
            for document_type, field in PROOFS[proof]:
                self.assertIn(field, REQUIRED_FIELDS[document_type])

    def test_proof_of_bpl_from_a_ration_card(self):
        record = {"document_id": "rc", "owner_id": "o", "type": "ration_card", "status": "confirmed",
                  "confirmed_fields": {key: {"value": "x", "source_ref": "s"} for key in REQUIRED_FIELDS["ration_card"]}}
        self.assertEqual(match_proofs(["proof_of_bpl"], [record], "o", "2026-11-21")[0]["status"], "usable")


if __name__ == "__main__":
    unittest.main()
