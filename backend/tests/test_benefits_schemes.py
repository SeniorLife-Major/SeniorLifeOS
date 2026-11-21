"""Tests: the four made-up demo schemes (tools/schemes.py) through the benefits graph, in both
modes: screening ("which schemes could I get?") and applying for one (benefits Part 2).

The story: Kamla Devi, born 12 March 1958 (68 on 21 Nov 2026), Maharashtra. Her vault holds an
Aadhaar, a ration card (her son is the head of the family; she is listed as a member) and a
passbook. The widow pension needs her husband's death certificate naming her as his wife.
"""

import unittest

from backend.graphs.benefits_graph import build_benefits_graph
from backend.tools.document_rules import DOCUMENT_TYPES
from backend.tools.schemes import (
    DOCUMENT_WORDS,
    FACT_DOCUMENT,
    SCHEMES,
    compare_names,
    listed_schemes,
)

OWNER = "senior-kamla"
AS_OF = "2026-11-21"
PAPERS_NEEDED = {"widow_pension", "old_age_pension", "disability_pension", "senior_health_cover"}


def record(document_id, document_type, fields, status="confirmed", owner=OWNER):
    return {
        "document_id": document_id, "owner_id": owner, "type": document_type, "status": status,
        "confirmed_fields": {key: {"value": value, "source_ref": f"document:{document_id}:page:1:line:{line}"}
                             for line, (key, value) in enumerate(fields.items(), start=1)},
        "expiry_date": None,
    }


def identity(**changes):
    fields = {"name": "Kamla Devi", "date_of_birth": "1958-03-12", "gender": "female",
              "aadhaar_number": "XXXX XXXX 0124"}
    return record("id", "identity", {**fields, **changes})


def ration_card(**changes):
    fields = {"head_of_family": "Raju Prasad", "card_category": "PHH", "residence_state": "MH",
              "family_members": "Raju Prasad, Kamla Devi, Sita Prasad", "ration_card_number": "XXXXXXXX6789"}
    return record("ration", "ration_card", {key: value for key, value in {**fields, **changes}.items()
                                            if value is not None})


def passbook():
    return record("bank", "bank_passbook", {"name": "Kamla Devi", "bank_name": "Demo Bank", "account_number":
                                            "XXXXXXXX1012", "ifsc": "DEMO0004321"})


def death_certificate(status="confirmed", **changes):
    fields = {"deceased_name": "Ramesh Prasad", "date_of_death": "2024-02-14", "issue_date": "2024-03-02",
              "issuing_authority": "Registrar, Demo Municipal Council", "spouse_name": "Kamla Devi",
              "registration_number": "XXXXXXXXXXX0012"}
    return record("death", "death_certificate", {key: value for key, value in {**fields, **changes}.items()
                                                 if value is not None}, status=status)


def disability(percent):
    return record("disability", "disability_certificate", {
        "name": "Kamla Devi", "issuing_authority": "Demo District Medical Board", "issue_date": "2025-01-10",
        "disability_percent": percent})


def kamla_papers():
    return [identity(), ration_card(), passbook()]


def screen(documents, **extra):
    state = {"actor_id": OWNER, "case_id": "check-1", "case_version": 1, "expected_case_version": 1,
             "mode": "screen", "as_of": AS_OF, "documents": documents, **extra}
    result = build_benefits_graph().invoke(state)
    return result, {item["scheme_id"]: item for item in result.get("scheme_assessments", [])}


def apply(scheme_id, documents, **extra):
    state = {"actor_id": OWNER, "case_id": "apply-1", "case_version": 1, "expected_case_version": 1,
             "scheme_id": scheme_id, "jurisdiction": "MH", "as_of": AS_OF, "documents": documents, **extra}
    return build_benefits_graph().invoke(state)


def criterion(assessment, criterion_id):
    return next(item for item in assessment["criteria"] if item["criterion_id"] == criterion_id)


BOTH = {"not_remarried": True, "no_other_pension": True}


class ScreeningTests(unittest.TestCase):
    def test_kamla_without_the_death_certificate_is_told_to_add_it(self):
        result, schemes = screen(kamla_papers())
        self.assertEqual(result["status"], "screened")
        self.assertEqual(set(schemes), PAPERS_NEEDED)
        widow = schemes["widow_pension"]
        self.assertEqual(widow["status"], "add_documents")
        self.assertEqual(criterion(widow, "widowed")["status"], "unknown")
        for met in ("woman", "age_from", "age_to", "poor_household"):
            self.assertEqual(criterion(widow, met)["status"], "met", met)
        self.assertEqual(widow["gaps"], [{"document_type": "death_certificate", "document_id": None,
                                          "reason": "missing", "message": "Add your husband's death certificate."}])
        self.assertEqual(schemes["old_age_pension"]["status"], "likely")
        self.assertEqual(schemes["senior_health_cover"]["status"], "not_eligible")  # 68, not 70
        self.assertEqual(criterion(schemes["senior_health_cover"], "age")["actual"], 68)

    def test_with_the_certificate_naming_her_the_widow_pension_is_likely(self):
        result, schemes = screen([*kamla_papers(), death_certificate()])
        widow = schemes["widow_pension"]
        self.assertEqual(widow["status"], "likely")
        self.assertEqual(widow["gaps"], [])
        widowed = criterion(widow, "widowed")
        self.assertEqual((widowed["status"], widowed["fact_source_ref"]), ("met", "document:death:page:1:line:5"))
        self.assertEqual([item["key"] for item in widow["to_confirm"]], ["not_remarried", "no_other_pension"])
        self.assertIn("You can get only one monthly pension. Choose the one that suits you best.", result["notes"])

    def test_a_near_spelling_of_her_name_is_sent_to_her_to_check(self):
        _, schemes = screen([*kamla_papers(), death_certificate(spouse_name="Kamala Devi")])
        widow = schemes["widow_pension"]
        self.assertEqual(widow["status"], "please_check")
        self.assertEqual(criterion(widow, "widowed")["status"], "conflict")
        self.assertEqual(widow["gaps"][0]["reason"], "check")
        self.assertIn("spelt a little differently", widow["gaps"][0]["message"])
        self.assertNotIn("Kamala", str(widow))  # results never quote names

    def test_a_certificate_naming_someone_else_is_not_accepted(self):
        _, schemes = screen([*kamla_papers(), death_certificate(spouse_name="Sunita Sharma")])
        self.assertEqual(schemes["widow_pension"]["status"], "please_check")
        self.assertIn("names someone else", schemes["widow_pension"]["gaps"][0]["message"])

    def test_a_certificate_without_the_spouse_name_asks_for_it(self):
        _, schemes = screen([*kamla_papers(), death_certificate(spouse_name=None)])
        widow = schemes["widow_pension"]
        self.assertEqual(widow["status"], "add_documents")
        self.assertEqual(widow["gaps"][0]["reason"], "detail_missing")
        self.assertEqual(widow["gaps"][0]["document_id"], "death")

    def test_a_certificate_still_waiting_for_review_counts_as_not_ready(self):
        _, schemes = screen([*kamla_papers(), death_certificate(status="needs_review")])
        widow = schemes["widow_pension"]
        self.assertEqual(widow["status"], "add_documents")
        self.assertEqual(widow["gaps"][0]["reason"], "unconfirmed")

    def test_an_apl_card_is_not_a_poor_household(self):
        _, schemes = screen([identity(), ration_card(card_category="APL"), passbook(), death_certificate()])
        for scheme_id in ("old_age_pension", "widow_pension", "disability_pension"):
            self.assertEqual(schemes[scheme_id]["status"], "not_eligible", scheme_id)
            self.assertEqual(criterion(schemes[scheme_id], "poor_household")["status"], "unmet")

    def test_a_card_whose_head_is_someone_else_needs_the_members(self):
        _, schemes = screen([identity(), ration_card(family_members=None), passbook()])
        old_age = schemes["old_age_pension"]
        self.assertEqual(old_age["status"], "add_documents")
        self.assertEqual(old_age["gaps"][0]["reason"], "detail_missing")
        self.assertIn("family members' names", old_age["gaps"][0]["message"])

    def test_a_card_she_heads_herself_is_enough(self):
        _, schemes = screen([identity(), ration_card(head_of_family="Smt. Kamla Devi", family_members=None),
                             passbook()])
        self.assertEqual(schemes["old_age_pension"]["status"], "likely")

    def test_a_card_that_does_not_name_her_is_questioned(self):
        _, schemes = screen([identity(), ration_card(family_members="Raju Prasad, Sita Prasad"), passbook()])
        self.assertEqual(schemes["old_age_pension"]["status"], "please_check")

    def test_widow_pension_is_for_women(self):
        _, schemes = screen([identity(gender="male"), ration_card(), passbook(), death_certificate()])
        self.assertEqual(schemes["widow_pension"]["status"], "not_eligible")
        self.assertEqual(criterion(schemes["widow_pension"], "woman")["status"], "unmet")

    def test_no_gender_on_the_id_asks_for_it(self):
        papers = [identity(), ration_card(), passbook(), death_certificate()]
        del papers[0]["confirmed_fields"]["gender"]
        _, schemes = screen(papers)
        widow = schemes["widow_pension"]
        self.assertEqual(widow["status"], "add_documents")
        self.assertEqual(widow["gaps"], [{"document_type": "identity", "document_id": "id", "reason": "detail_missing",
                                          "message": "Your ID card's details do not say if you are a man or a woman. "
                                                     "Add it as printed on the card."}])

    def test_health_cover_at_seventy_needs_only_the_id(self):
        _, schemes = screen([identity(date_of_birth="1954-01-01")])
        cover = schemes["senior_health_cover"]
        self.assertEqual(cover["status"], "likely")
        self.assertEqual(cover["to_confirm"], [])
        self.assertEqual(schemes["old_age_pension"]["status"], "add_documents")
        self.assertEqual([gap["document_type"] for gap in schemes["old_age_pension"]["gaps"]],
                         ["ration_card", "bank_passbook"])

    def test_disability_pension_needs_eighty_percent(self):
        _, schemes = screen([*kamla_papers(), disability(85)])
        self.assertEqual(schemes["disability_pension"]["status"], "likely")
        _, schemes = screen([*kamla_papers(), disability(40)])
        self.assertEqual(schemes["disability_pension"]["status"], "not_eligible")

    def test_papers_with_different_names_stop_every_scheme(self):
        papers = [identity(), ration_card(), passbook()]
        papers[2]["confirmed_fields"]["name"]["value"] = "Sunita Sharma"
        _, schemes = screen(papers)
        for item in schemes.values():
            self.assertEqual(item["status"], "please_check", item["scheme_id"])

    def test_someone_elses_papers_are_ignored(self):
        _, schemes = screen([identity(), ration_card(), passbook(),
                             {**death_certificate(), "owner_id": "someone-else"}])
        self.assertEqual(schemes["widow_pension"]["status"], "add_documents")

    def test_the_first_demo_scheme_is_not_listed(self):
        result, schemes = screen(kamla_papers(), jurisdiction="MH")
        self.assertNotIn("demo_senior_support", schemes)

    def test_screening_can_be_limited_to_some_schemes(self):
        _, schemes = screen(kamla_papers(), scheme_ids=["widow_pension"])
        self.assertEqual(set(schemes), {"widow_pension"})

    def test_a_catalogue_older_than_a_year_is_refused(self):
        result, _ = screen(kamla_papers(), as_of="2027-12-01")
        self.assertEqual(result["status"], "blocked_source_review")


class ApplyTests(unittest.TestCase):
    def test_declarations_are_asked_for_before_a_package(self):
        result = apply("widow_pension", [*kamla_papers(), death_certificate()])
        self.assertEqual(result["status"], "needs_input")
        self.assertIn("Confirm: I have not married again", result["missing_inputs"])
        self.assertNotIn("package_proposal", result)

    def test_with_her_papers_and_declarations_a_package_waits_for_her_approval(self):
        result = apply("widow_pension", [*kamla_papers(), death_certificate()], declarations=BOTH)
        self.assertEqual(result["status"], "awaiting_review")
        proposal = result["package_proposal"]
        self.assertEqual(proposal["document_ids"], ["id", "death", "ration", "bank"])
        self.assertEqual(proposal["declarations"], ["I have not married again",
                                                    "I do not get another government pension"])
        self.assertTrue(any("ward office" in step for step in proposal["checklist"]))

    def test_approval_makes_the_handoff_package(self):
        result = apply("widow_pension", [*kamla_papers(), death_certificate()], declarations=BOTH,
                       review_decision={"action": "approve", "reviewer_id": OWNER})
        self.assertEqual(result["status"], "ready_for_manual_handoff")
        self.assertEqual(result["application_package"]["status"], "approved_for_manual_handoff")
        self.assertEqual(len(result["created_tasks"]), 1)

    def test_someone_else_cannot_approve(self):
        result = apply("widow_pension", [*kamla_papers(), death_certificate()], declarations=BOTH,
                       review_decision={"action": "approve", "reviewer_id": "family-raju"})
        self.assertEqual(result["status"], "rejected")

    def test_a_changed_paper_changes_the_action_id(self):
        first = apply("old_age_pension", kamla_papers(), declarations=BOTH)["package_proposal"]["action_id"]
        papers = kamla_papers()
        papers[1]["confirmed_fields"]["card_category"]["value"] = "AAY"
        second = apply("old_age_pension", papers, declarations=BOTH)["package_proposal"]["action_id"]
        self.assertNotEqual(first, second)
        self.assertEqual(first, apply("old_age_pension", kamla_papers(), declarations=BOTH)["package_proposal"]["action_id"])

    def test_not_eligible_stops_before_a_package(self):
        result = apply("senior_health_cover", kamla_papers())
        self.assertEqual(result["status"], "not_eligible_in_demo")

    def test_a_national_scheme_covers_every_state(self):
        result = apply("senior_health_cover", [identity(date_of_birth="1950-01-01")], jurisdiction="KL",
                       review_decision={"action": "approve", "reviewer_id": OWNER})
        self.assertEqual(result["status"], "ready_for_manual_handoff")


class CatalogueTests(unittest.TestCase):
    def test_the_four_demo_schemes_are_listed_and_well_formed(self):
        self.assertEqual({scheme["scheme_id"] for scheme in listed_schemes()}, PAPERS_NEEDED)
        for scheme in SCHEMES.values():
            with self.subTest(scheme=scheme["scheme_id"]):
                self.assertTrue(set(scheme["required_documents"]) <= set(DOCUMENT_TYPES))
                self.assertTrue(set(scheme["required_documents"]) <= set(DOCUMENT_WORDS))
                for rule in scheme["criteria"]:
                    self.assertIn(rule["fact"], FACT_DOCUMENT)
                    self.assertTrue(rule.get("label"))
                if scheme.get("listed"):
                    self.assertIn("(demo)", scheme["name"])  # never mistaken for a real programme

    def test_names(self):
        self.assertEqual(compare_names("Kamla Devi", "Smt. KAMLA DEVI"), "same")
        self.assertEqual(compare_names("Kamla Devi", "Kamala Devi"), "close")
        self.assertEqual(compare_names("Kamla Devi", "Kamla Devi Prasad"), "close")
        self.assertEqual(compare_names("Lalita Rao", "LaIita Rao"), "close")  # I for l
        self.assertEqual(compare_names("Raju Prasad", "Sita Prasad"), "different")
        self.assertEqual(compare_names("Kamla Devi", ""), "different")


if __name__ == "__main__":
    unittest.main()
