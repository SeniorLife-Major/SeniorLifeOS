"""POST /api/v1/documents/match (fastapi-backend Phase 3): which of the owner's documents meet a
list of needs, by the shared rules in tools/document_rules. Real PostgreSQL (TEST_DATABASE_URL);
OCR is faked.
"""

from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from backend.api import documents
from backend.errors import ValidationException
from backend.storage.db import make_sessions
from backend.storage.service import DocumentService, india_today
from test_documents_api import ApiCase
from test_privacy import FAKE_HASH_KEY, hash_key_env
from test_storage_db import IDENTITY, OTHER_OWNER, OWNER, UNSURE_IDENTITY, DatabaseCase

INSURANCE = {"name": "Meera Rao", "insurer": "Demo Health Insurance Co. Ltd.", "policy_number": "HLT/2026/556677",
             "expiry_date": "31-03-2026", "sum_insured": "5,00,000"}
INCOME = {"name": "Ravi Rao", "annual_income": "1,20,000", "issue_date": "01-06-2026"}


class MatchCase(DatabaseCase):
    def type_in(self, document_type, fields, owner=OWNER):
        with patch("backend.utils.privacy.find_env_file", return_value=None), hash_key_env(FAKE_HASH_KEY):
            return self.service.create_manual(owner, document_type, fields)["document_id"]

    def statuses(self, result):
        return ({item["required_type"]: (item["status"], item["document_id"]) for item in result["matches"]},
                {item["proof"]: (item["status"], item["document_type"], item["document_id"])
                 for item in result["proofs"]})


class MatchServiceTests(MatchCase):
    def test_each_need_gets_the_owners_best_document(self):
        identity = self.upload(IDENTITY)["document_id"]
        insurance = self.type_in("health_insurance", INSURANCE)
        self.type_in("income_certificate", INCOME, owner=OTHER_OWNER)  # someone else's never counts
        needs = (["identity", "health_insurance", "income_certificate"], ["proof_of_age", "proof_of_income"])

        types, proofs = self.statuses(self.service.match(OWNER, *needs, as_of=date(2026, 1, 1)))
        self.assertEqual(types, {"identity": ("usable", identity), "health_insurance": ("usable", insurance),
                                 "income_certificate": ("missing", None)})
        self.assertEqual(proofs, {"proof_of_age": ("usable", "identity", identity),
                                  "proof_of_income": ("missing", None, None)})
        types, _ = self.statuses(self.service.match(OWNER, *needs, as_of=date(2026, 11, 18)))
        self.assertEqual(types["health_insurance"], ("expired", insurance))  # ended 31 March 2026

    def test_the_answer_holds_no_values(self):
        self.upload(IDENTITY)
        self.type_in("health_insurance", INSURANCE)
        result = self.service.match(OWNER, ["identity", "health_insurance"], ["proof_of_identity"])
        text = json.dumps(result, default=str)
        for value in ("Meera", "HLT", "556677", "500000", "1950"):
            self.assertNotIn(value, text)

    def test_unconfirmed_means_still_on_its_way(self):
        waiting = self.upload(UNSURE_IDENTITY)["document_id"]
        types, proofs = self.statuses(self.service.match(OWNER, ["identity"], ["proof_of_age"]))
        self.assertEqual(types["identity"], ("unconfirmed", waiting))
        self.assertEqual(proofs["proof_of_age"], ("unconfirmed", "identity", waiting))

        confirmed = self.upload(IDENTITY)["document_id"]  # a usable one wins
        self.assertEqual(self.statuses(self.service.match(OWNER, ["identity"], []))[0]["identity"],
                         ("usable", confirmed))

    def test_a_rejected_or_failed_document_counts_as_missing(self):
        rejected = self.upload(UNSURE_IDENTITY)["document_id"]
        self.review(rejected, action="reject")
        with self.ocr(IDENTITY) as reader, self.assertLogs("backend.storage.service", level="ERROR"):
            reader.side_effect = RuntimeError("the reader broke")
            with self.assertRaises(Exception):
                self.service.upload(OWNER, "card.png", "image/png", self.png, claimed_type="identity")
        self.assertEqual(sorted(item["status"] for item in self.service.list(OWNER)), ["failed_tool", "rejected"])
        self.assertEqual(self.statuses(self.service.match(OWNER, ["identity"], []))[0]["identity"],
                         ("missing", None))

    def test_a_document_being_read_in_the_background_is_unconfirmed(self):
        reader = DocumentService(make_sessions(self.engine), self.files, self.checkpointer)
        reader.background = SimpleNamespace(submit=lambda *args, **kwargs: None)  # still waiting for a reader
        with self.ocr(IDENTITY):
            waiting = reader.upload(OWNER, "card.png", "image/png", self.png, claimed_type="identity",
                                    background=True)["document_id"]
        self.assertEqual(self.statuses(self.service.match(OWNER, ["identity"], []))[0]["identity"],
                         ("unconfirmed", waiting))

    def test_a_renewal_waiting_for_review_keeps_the_old_version_usable(self):
        identity = self.upload(IDENTITY)["document_id"]
        with self.ocr(UNSURE_IDENTITY):
            renewal = self.service.replace_upload(OWNER, identity, "new.png", "image/png", self.png)
        self.assertEqual(renewal["replacement"]["status"], "needs_review")
        self.assertEqual(self.statuses(self.service.match(OWNER, ["identity"], []))[0]["identity"],
                         ("usable", identity))

    def test_each_need_once_and_only_known_ones(self):
        result = self.service.match(OWNER, ["identity", "identity"], ["proof_of_age", "proof_of_age"],
                                    as_of=date(2026, 1, 1))
        self.assertEqual((len(result["matches"]), len(result["proofs"])), (1, 1))
        for requirements, proofs in ((["passport"], []), ([], ["proof_of_wealth"]), ([], [])):
            with self.subTest(requirements=requirements, proofs=proofs), self.assertRaises(ValidationException):
                self.service.match(OWNER, requirements, proofs)

    def test_by_default_the_day_is_today_in_india(self):
        class LateEvening(datetime):  # 18 Nov, 20:00 UTC is already 19 Nov, 01:30 in India
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 11, 18, 20, 0, tzinfo=timezone.utc).astimezone(tz)

        with patch("backend.storage.service.datetime", LateEvening):
            self.assertEqual(india_today(), date(2026, 11, 19))
            self.type_in("health_insurance", {**INSURANCE, "expiry_date": "18-11-2026"})
            result = self.service.match(OWNER, ["health_insurance"], [])
        self.assertEqual(result["as_of"], date(2026, 11, 19))
        self.assertEqual(result["matches"][0]["status"], "expired")  # it ended yesterday, in India


class MatchApiTests(MatchCase, ApiCase):
    def setUp(self):
        MatchCase.setUp(self)
        ApiCase.setUp(self)
        self.app.dependency_overrides[documents.get_document_service] = lambda: self.service

    def post(self, body, owner=OWNER):
        return self.client.post("/api/v1/documents/match", json=body, headers={"X-Actor-Id": owner})

    def test_match_over_http(self):
        identity = self.upload(IDENTITY)["document_id"]
        response = self.post({"requirements": ["identity", "income_certificate"], "proofs": ["proof_of_age"],
                              "asOf": "2026-11-18"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.json()["data"], {
            "asOf": "2026-11-18",
            "matches": [{"requiredType": "identity", "documentId": identity, "status": "usable"},
                        {"requiredType": "income_certificate", "documentId": None, "status": "missing"}],
            "proofs": [{"proof": "proof_of_age", "documentType": "identity", "documentId": identity,
                        "status": "usable"}]})
        stranger = self.post({"requirements": ["identity"]}, owner=OTHER_OWNER).json()["data"]
        self.assertEqual(stranger["matches"], [{"requiredType": "identity", "documentId": None, "status": "missing"}])

    def test_bad_requests(self):
        for body in ({}, {"requirements": ["passport"]}, {"proofs": ["proof_of_wealth"]},
                     {"requirements": ["identity"] * 21}, {"requirements": ["identity"], "asOf": "18/11/2026"},
                     {"requirements": "identity"}):
            with self.subTest(body=body):
                self.assertEqual(self.post(body).status_code, 422)


if __name__ == "__main__":
    unittest.main()
