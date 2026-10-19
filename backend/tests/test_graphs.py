"""Behavior tests for the local graph branches and approval boundaries."""

import unittest

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command


from backend.graphs.benefits_graph import build_benefits_graph
from backend.graphs.document_graph import build_document_graph
from backend.demo.demo_cases import (
    benefits_failure_state,
    benefits_success_state,
    document_failure_state,
    document_success_state,
)


class DocumentGraphTests(unittest.TestCase):
    def test_corrected_income_certificate_is_confirmed_with_provenance(self):
        result = build_document_graph().invoke(document_success_state())
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["vault_record"]["confirmed_fields"]["annual_income"]["value"], 120000)
        self.assertTrue(result["vault_record"]["confirmed_fields"]["issue_date"]["corrected_by_user"])
        self.assertIn("page:1:line:2", result["vault_record"]["confirmed_fields"]["annual_income"]["source_ref"])
        self.assertIn("extract_content:retry_1", result["events"])

    def test_blurry_page_requests_reupload(self):
        result = build_document_graph().invoke(document_failure_state())
        self.assertEqual(result["status"], "needs_reupload")
        self.assertNotIn("vault_record", result)

    def test_unapproved_document_is_not_published(self):
        state = document_success_state()
        state.pop("review_decision")
        result = build_document_graph().invoke(state)
        self.assertEqual(result["status"], "needs_review")
        self.assertNotIn("vault_record", result)

    def test_interrupt_can_resume_with_review(self):
        graph = build_document_graph(checkpointer=InMemorySaver())
        state = document_success_state()
        state.pop("review_decision")
        state["use_interrupt"] = True
        config = {"configurable": {"thread_id": "test-document-interrupt"}}
        pending = graph.invoke(state, config)
        self.assertIn("__interrupt__", pending)
        finished = graph.invoke(Command(resume={
            "action": "confirm", "reviewer_id": "senior-meera", "edits": {"issue_date": "2026-06-01"},
        }), config)
        self.assertEqual(finished["status"], "confirmed")

    def test_clean_high_confidence_document_is_auto_accepted(self):
        state = document_success_state()
        state.pop("review_decision")
        state["document"]["pages"][0]["text"] = state["document"]["pages"][0]["text"].replace("2026/06/01", "2026-06-01")
        result = build_document_graph().invoke(state)
        self.assertEqual(result["status"], "confirmed")
        self.assertIn("confidence_gate:auto_accept", result["events"])
        self.assertFalse(any(event.startswith("human_field_review") for event in result["events"]))
        income = result["vault_record"]["confirmed_fields"]["annual_income"]
        self.assertEqual(income["reviewed_by"], "system:confidence_gate")
        self.assertFalse(income["corrected_by_user"])

    def test_low_confidence_field_is_sent_to_review(self):
        state = document_success_state()
        state.pop("review_decision")
        state["document"]["pages"][0]["text"] = state["document"]["pages"][0]["text"].replace("2026/06/01", "2026-06-01")
        state["document"]["pages"][0]["quality"] = 0.7
        result = build_document_graph().invoke(state)
        self.assertEqual(result["status"], "needs_review")
        self.assertIn("confidence_gate:needs_review", result["events"])
        self.assertIn("annual_income: low confidence (0.70)", result["review_flags"])
        self.assertNotIn("vault_record", result)

    def test_owner_reject_wins_over_auto_accept(self):
        state = document_success_state()
        state["document"]["pages"][0]["text"] = state["document"]["pages"][0]["text"].replace("2026/06/01", "2026-06-01")
        state["review_decision"] = {"action": "reject", "reviewer_id": "senior-meera"}
        result = build_document_graph().invoke(state)
        self.assertEqual(result["status"], "rejected")
        self.assertIn("confidence_gate:owner_decision", result["events"])
        self.assertNotIn("vault_record", result)


class BenefitsGraphTests(unittest.TestCase):
    def test_confirmed_document_flows_into_approved_package(self):
        document = build_document_graph().invoke(document_success_state())["vault_record"]
        result = build_benefits_graph().invoke(benefits_success_state(document))
        self.assertEqual(result["status"], "ready_for_manual_handoff")
        self.assertTrue(all(row["status"] == "met" for row in result["criterion_results"]))
        self.assertIn(document["document_id"], result["application_package"]["document_ids"])
        self.assertEqual(len(result["created_tasks"]), 1)

    def test_missing_income_is_unknown_and_blocks_package(self):
        result = build_benefits_graph().invoke(benefits_failure_state())
        self.assertEqual(result["status"], "needs_input")
        self.assertNotIn("application_package", result)
        income = next(row for row in result["criterion_results"] if row["criterion_id"] == "annual_income")
        self.assertEqual(income["status"], "unknown")

    def test_expired_income_document_does_not_satisfy_requirement(self):
        document = build_document_graph().invoke(document_success_state())["vault_record"]
        document["expiry_date"] = "2026-01-01"
        result = build_benefits_graph().invoke(benefits_success_state(document))
        self.assertEqual(result["status"], "needs_input")
        income_match = next(row for row in result["document_matches"] if row["required_type"] == "income_certificate")
        self.assertEqual(income_match["status"], "expired")

    def test_conflicting_confirmed_income_blocks_package(self):
        document = build_document_graph().invoke(document_success_state())["vault_record"]
        state = benefits_success_state(document)
        state["profile_facts"] = {
            "annual_income": {"value": 300000, "confirmed": True, "source_ref": "profile:income:old"},
        }
        result = build_benefits_graph().invoke(state)
        self.assertEqual(result["status"], "blocked_conflict")
        self.assertNotIn("application_package", result)

    def test_source_outage_blocks_assessment(self):
        state = benefits_failure_state()
        state["simulate_source_unavailable"] = True
        result = build_benefits_graph().invoke(state)
        self.assertEqual(result["status"], "blocked_source_review")
        self.assertNotIn("criterion_results", result)

    def test_benefits_interrupt_can_resume_with_approval(self):
        document = build_document_graph().invoke(document_success_state())["vault_record"]
        state = benefits_success_state(document)
        state.pop("review_decision")
        state["use_interrupt"] = True
        graph = build_benefits_graph(checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": "test-benefits-interrupt"}}
        pending = graph.invoke(state, config)
        self.assertIn("__interrupt__", pending)
        finished = graph.invoke(Command(resume={
            "action": "approve", "reviewer_id": "senior-meera",
        }), config)
        self.assertEqual(finished["status"], "ready_for_manual_handoff")


if __name__ == "__main__":
    unittest.main()
