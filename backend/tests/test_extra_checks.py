"""Tests for the extra checks: misread organization names, and proofs that map to document types."""

import json
import unittest
from unittest.mock import patch

from backend.graphs.document_graph import build_document_graph
from backend.tools import document_rules
from backend.tools.document_rules import PROOFS, REQUIRED_FIELDS, match_proofs
from backend.tools.free_text import free_text_problems, looks_cut_off, near_miss
from test_document_rules import AS_OF, OWNER, vault_doc
from test_graph_ocr import ocr_page, upload_state
from test_ocr import UNIT, ensure_fixtures
from test_privacy import FAKE_HASH_KEY, hash_key_env

HARD_RESULTS = UNIT.parent / "ocr_hard" / "results_paddle.json"

# Names that must pass: the fake datasets' own values and common real Indian organizations.
GOOD_NAMES = [
    "Medical Board, Demo District Hospital", "DEMO DIAGNOSTICS & PATHOLOGY LAB", "DEMO CITY HOSPITAL PHARMACY",
    "DEMO CO-OPERATIVE BANK LTD.", "Demo Health Insurance Co. Ltd.", "Demo District Office",
    "State Bank of India", "Punjab National Bank", "HDFC Bank Ltd", "Apollo Hospitals", "Bank of Baroda",
    "Office of the Tahsildar, Pune", "Star Health and Allied Insurance Co. Ltd.",
    "Life Insurance Corporation of India", "Sub-Divisional Magistrate, Thane", "Metropolis Healthcare Ltd",
    "Kokilaben Dhirubhai Ambani Hospital", "Civil Surgeon, District Hospital, Tamil Nadu",
]


class FreeTextTests(unittest.TestCase):
    def test_ordinary_names_raise_nothing(self):
        for name in GOOD_NAMES:
            with self.subTest(name=name):
                self.assertEqual(free_text_problems("issuing_authority", name), [])

    def test_cut_off_values(self):
        for name in ("Medical Board, Demo D", "Office of the", "Demo District Hospital,", "Demo Diagnostics &",
                     "Office of"):
            with self.subTest(name=name):
                self.assertTrue(looks_cut_off(name))
                self.assertIn("may be cut off", free_text_problems("issuing_authority", name))

    def test_near_misses_of_known_words(self):
        cases = {"Distriet": "district", "Medlcal": "medical", "Hosptal": "hospital", "Insurence": "insurance",
                 "Boards": None, "Hospitals": None}  # plurals of listed words are fine
        for word, known in cases.items():
            with self.subTest(word=word):
                self.assertEqual(near_miss(word), known)

    def test_place_and_brand_names_far_from_every_word_pass(self):
        for word in ("Pune", "Thane", "Kokilaben", "Dhirubhai", "Metropolis", "Baroda", "Fortis"):
            with self.subTest(word=word):
                self.assertIsNone(near_miss(word))

    def test_only_organization_fields_are_checked_and_flags_never_quote_the_value(self):
        self.assertEqual(free_text_problems("name", "Demo D"), [])
        self.assertEqual(free_text_problems("issuing_authority", 42), [])
        notes = free_text_problems("bank_name", "Demo Distriet Bank,")
        self.assertEqual(notes, ["may be cut off", "a word may be misread"])
        self.assertFalse(any("Distriet" in note for note in notes))


@patch("backend.utils.privacy.find_env_file", return_value=None)
class GraphFreeTextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_fixtures()

    def run_upload(self, authority):
        page = ocr_page([("Name: Meera Rao", 0.99), (f"Issuing authority: {authority}", 0.98),
                         ("Issue date: 10/11/2025", 0.99), ("Disability: 40%", 0.99)])
        with patch("backend.graphs.document_graph.read_document", return_value=[page]), hash_key_env(FAKE_HASH_KEY):
            return build_document_graph().invoke(upload_state(UNIT / "sharp.png", "image/png",
                                                              "disability_certificate"))

    def test_a_sure_but_cut_off_issuer_is_no_longer_auto_accepted(self, _):
        result = self.run_upload("Medical Board, Demo D")
        self.assertEqual(result["status"], "needs_review")
        self.assertIn("issuing_authority: may be cut off, please check", result["review_flags"])

    def test_a_misread_word_goes_to_review(self, _):
        result = self.run_upload("Medical Board, Demo Distriet Hospital")
        self.assertEqual(result["status"], "needs_review")
        self.assertIn("issuing_authority: a word may be misread, please check", result["review_flags"])

    def test_a_clean_issuer_is_still_auto_accepted(self, _):
        result = self.run_upload("Medical Board, Demo District Hospital")
        self.assertEqual(result["status"], "confirmed")

    def test_real_ocr_output_of_the_hard_set(self, _):
        # Decision #26: the two issuer errors PaddleOCR made at 0.97 confidence are now flagged,
        # and none of the correct organization names on the other 52 pages are.
        if not HARD_RESULTS.exists():
            self.skipTest("hard dataset results missing (run tests/make_hard_dataset.py --score)")
        from backend.tools.demo_tools import extract_fields_from_pages
        from backend.tools.medical import heading_fields
        from backend.tools.ocr_postprocess import clean_page
        flagged = set()
        for saved in json.loads(HARD_RESULTS.read_text(encoding="utf-8"))["pages"]:
            page = clean_page({"page": 1, "text": saved["text"], "lines": saved["lines"]})
            found = extract_fields_from_pages([page], saved["type"])
            found.update(heading_fields([page], saved["type"], found))
            for key, item in found.items():
                if free_text_problems(key, item["value"]):
                    flagged.add(saved["path"])
        self.assertEqual(flagged, {"pages/disability_certificate__phone_glare_shake.png",
                                   "pages/disability_certificate__worn.png"})


class ProofTests(unittest.TestCase):
    def test_every_proving_field_is_a_required_field_of_its_type(self):
        # match_proofs relies on this: a usable document then always has the proving field.
        for proof, accepted in PROOFS.items():
            for document_type, field in accepted:
                with self.subTest(proof=proof, document_type=document_type):
                    self.assertIn(field, REQUIRED_FIELDS[document_type])

    def test_proof_of_age_is_met_by_a_confirmed_identity_document(self):
        result = match_proofs(["proof_of_age", "proof_of_income"], [vault_doc("id-1", "identity")], OWNER, AS_OF)
        self.assertEqual(result, [
            {"proof": "proof_of_age", "document_type": "identity", "document_id": "id-1", "status": "usable"},
            {"proof": "proof_of_income", "document_type": None, "document_id": None, "status": "missing"},
        ])

    def test_same_rules_as_a_fixed_type(self):
        cases = [(vault_doc("id-1", "identity", status="pending"), "unconfirmed"),
                 (vault_doc("id-1", "identity", drop_field="date_of_birth"), "unconfirmed"),
                 (vault_doc("id-1", "identity", owner_id="someone-else"), "missing")]
        for document, status in cases:
            with self.subTest(status=status):
                self.assertEqual(match_proofs(["proof_of_age"], [document], OWNER, AS_OF)[0]["status"], status)

    def test_expired_cover_is_reported_as_expired(self):
        document = vault_doc("ins-1", "health_insurance", expiry_date="2026-01-01")
        self.assertEqual(match_proofs(["health_cover"], [document], OWNER, AS_OF)[0]["status"], "expired")

    def test_the_best_accepted_type_wins(self):
        two_ways = {"proof_of_age": (("identity", "date_of_birth"), ("disability_certificate", "name"))}
        documents = [vault_doc("id-1", "identity", status="pending"), vault_doc("dis-1", "disability_certificate")]
        with patch.dict(document_rules.PROOFS, two_ways):
            best = match_proofs(["proof_of_age"], documents, OWNER, AS_OF)[0]
        self.assertEqual((best["document_type"], best["status"]), ("disability_certificate", "usable"))

    def test_unknown_proof_is_an_error_not_missing(self):
        with self.assertRaises(KeyError):
            match_proofs(["proof_of_magic"], [], OWNER, AS_OF)


if __name__ == "__main__":
    unittest.main()
