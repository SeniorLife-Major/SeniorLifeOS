"""Tests for the shared document rules and requirement matcher."""

import typing
import unittest

from backend.agents.llm_agents import DocumentTypeChoice
from backend.graphs import document_graph
from backend.graphs.benefits_graph import match_document_requirements
from backend.tools.document_rules import DOCUMENT_TYPES, REQUIRED_FIELDS, match_requirements


OWNER = "senior-meera"
AS_OF = "2026-09-22"
ALL_TYPES = ["identity", "income_certificate", "residence_proof"]


def vault_doc(document_id, document_type, owner_id=OWNER, status="confirmed", expiry_date=None, drop_field=None):
    """A vault record with every required field for its type, optionally missing one."""
    fields = {
        key: {"value": "x", "source_ref": f"document:{document_id}:page:1:line:{line}"}
        for line, key in enumerate(sorted(REQUIRED_FIELDS[document_type]), start=1)
        if key != drop_field
    }
    return {
        "document_id": document_id,
        "owner_id": owner_id,
        "type": document_type,
        "status": status,
        "confirmed_fields": fields,
        "expiry_date": expiry_date,
    }


def status_of(matches, document_type):
    return next(m for m in matches if m["required_type"] == document_type)


class MatchRequirementsTests(unittest.TestCase):
    def test_each_outcome(self):
        documents = [
            vault_doc("id-1", "identity"),
            vault_doc("inc-1", "income_certificate", status="pending_review"),
            vault_doc("res-1", "residence_proof", expiry_date="2026-01-01"),
        ]
        matches = match_requirements(ALL_TYPES, documents, OWNER, AS_OF)
        self.assertEqual(status_of(matches, "identity"), {"required_type": "identity", "document_id": "id-1", "status": "usable"})
        self.assertEqual(status_of(matches, "income_certificate")["status"], "unconfirmed")
        self.assertEqual(status_of(matches, "residence_proof")["status"], "expired")

    def test_missing_when_no_document_of_that_type(self):
        matches = match_requirements(["income_certificate"], [vault_doc("id-1", "identity")], OWNER, AS_OF)
        self.assertEqual(matches, [{"required_type": "income_certificate", "document_id": None, "status": "missing"}])

    def test_other_peoples_documents_are_ignored(self):
        matches = match_requirements(["identity"], [vault_doc("id-other", "identity", owner_id="someone-else")], OWNER, AS_OF)
        self.assertEqual(matches[0]["status"], "missing")
        self.assertIsNone(matches[0]["document_id"])

    def test_usable_copy_beats_expired_copy(self):
        documents = [
            vault_doc("inc-old", "income_certificate", expiry_date="2025-12-31"),
            vault_doc("inc-new", "income_certificate"),
        ]
        match = match_requirements(["income_certificate"], documents, OWNER, AS_OF)[0]
        self.assertEqual((match["status"], match["document_id"]), ("usable", "inc-new"))

    def test_missing_required_field_is_unconfirmed(self):
        match = match_requirements(["identity"], [vault_doc("id-1", "identity", drop_field="date_of_birth")], OWNER, AS_OF)[0]
        self.assertEqual(match["status"], "unconfirmed")

    def test_bad_expiry_date_is_unconfirmed(self):
        match = match_requirements(["identity"], [vault_doc("id-1", "identity", expiry_date="31/12/2030")], OWNER, AS_OF)[0]
        self.assertEqual(match["status"], "unconfirmed")


class CopiesAgreeTests(unittest.TestCase):
    """The graphs keep their own copies of the rules; these tests catch drift."""

    def test_document_graph_copy_matches(self):
        self.assertEqual(document_graph.REQUIRED_FIELDS, REQUIRED_FIELDS)

    def test_llm_type_choices_match(self):
        allowed = set(typing.get_args(DocumentTypeChoice.model_fields["document_type"].annotation))
        self.assertEqual(allowed, set(DOCUMENT_TYPES) | {"unknown"})

    def test_benefits_graph_matcher_gives_same_answers(self):
        scenarios = [
            [],
            [vault_doc("id-1", "identity"), vault_doc("inc-1", "income_certificate"), vault_doc("res-1", "residence_proof")],
            [vault_doc("inc-1", "income_certificate", status="pending_review"), vault_doc("res-1", "residence_proof", expiry_date="2026-01-01")],
            [vault_doc("inc-old", "income_certificate", expiry_date="2025-12-31"), vault_doc("inc-new", "income_certificate")],
            [vault_doc("id-1", "identity", drop_field="name"), vault_doc("id-2", "identity", expiry_date="bad-date")],
            [vault_doc("id-other", "identity", owner_id="someone-else")],
        ]
        for documents in scenarios:
            state = {
                "actor_id": OWNER,
                "as_of": AS_OF,
                "documents": documents,
                "scheme": {"required_documents": ALL_TYPES},
                "source_snapshot": {"source_id": "test-source"},
            }
            from_graph = match_document_requirements(state)["document_matches"]
            without_source = [{k: v for k, v in m.items() if k != "rule_source_id"} for m in from_graph]
            self.assertEqual(without_source, match_requirements(ALL_TYPES, documents, OWNER, AS_OF), documents)


if __name__ == "__main__":
    unittest.main()
