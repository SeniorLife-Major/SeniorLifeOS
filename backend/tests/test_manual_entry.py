"""Tests: documents the owner types in, with no file and no OCR (Decision #42).

Graph (take_manual_entry), storage (DocumentService.create_manual on the test database)
and API (POST /api/v1/documents/manual).
"""

import json
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from sqlalchemy import func, select

from backend.api import documents
from backend.errors import ValidationException
from backend.graphs.document_graph import build_document_graph
from backend.storage.models import AuditEvent, Document, IdentifierHash
from backend.storage.service import WorkflowFailed
from backend.utils.privacy import keyed_hash
from test_documents_api import DOCUMENT, ApiCase, FakeService
from test_graph_ocr import AADHAAR
from test_privacy import FAKE_HASH_KEY, hash_key_env
from test_storage_db import IDENTITY, OTHER_OWNER, OWNER, PAN, DatabaseCase

DIGITS = AADHAAR.replace(" ", "")
TYPED_IDENTITY = {"name": "Smt. Meera Rao", "date_of_birth": "17/09/1950", "aadhaar_number": AADHAAR,
                  "pan_number": PAN}
TYPED_PRESCRIPTION = {
    "name": "Meera Rao", "doctor_name": "Dr. Anil Kumar", "prescription_date": "01/09/2026",
    "medicines": [{"name": "Metformin", "form": "tablet", "strength": "500 mg", "frequency": "1-0-1",
                   "duration": "30 days"}],
}


@contextmanager
def hash_key(key=FAKE_HASH_KEY):
    with patch("backend.utils.privacy.find_env_file", return_value=None), hash_key_env(key):
        yield


def manual_state(document_type, fields, **extra):
    return {"actor_id": OWNER, "document_id": "doc-m1", "claimed_type": document_type, "manual_fields": fields,
            "document": {"owner_id": OWNER, "as_of": "2026-10-27"}, **extra}


class GraphTests(unittest.TestCase):
    def run_graph(self, document_type, fields, **extra):
        with hash_key():
            return build_document_graph().invoke(manual_state(document_type, fields, **extra))

    def test_typed_in_identity_is_checked_masked_hashed_and_confirmed(self):
        result = self.run_graph("identity", TYPED_IDENTITY)
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["events"][:2], ["validate_upload:manual", "take_manual_entry:4_fields"])
        self.assertFalse(any(event.startswith(("check_file", "read_file", "extract_content", "cloud_ocr",
                                               "vision_extract")) for event in result["events"]))
        fields = result["vault_record"]["confirmed_fields"]
        self.assertEqual(fields["name"]["value"], "Meera Rao")  # same checks as a value read from a page
        self.assertEqual(fields["date_of_birth"]["value"], "1950-09-17")
        self.assertEqual(fields["aadhaar_number"]["value"], "XXXX XXXX 7973")
        self.assertEqual(fields["pan_number"]["value"], "XXXXXX814K")
        self.assertEqual(fields["name"]["source_ref"], "document:doc-m1:entered_by_owner")
        self.assertEqual(fields["name"]["reviewed_by"], OWNER)
        self.assertEqual(result["vault_record"]["identifier_hashes"]["aadhaar_number"],
                         keyed_hash("aadhaar_number", DIGITS, FAKE_HASH_KEY))
        self.assertEqual(result["manual_fields"], {})
        text = json.dumps(result, default=str)
        for number in (AADHAAR, DIGITS, PAN):
            self.assertNotIn(number, text)

    def test_typed_in_prescription_keeps_its_rows(self):
        result = self.run_graph("prescription", TYPED_PRESCRIPTION)
        self.assertEqual(result["status"], "confirmed")
        row = result["vault_record"]["confirmed_fields"]["medicines"]["value"][0]
        self.assertEqual((row["name"], row["strength"], row["frequency"]), ("Metformin", "500 mg", "1-0-1"))

    def test_typed_in_bill_may_list_its_items(self):
        bill = {"name": "Meera Rao", "provider": "Demo Care Hospital", "bill_date": "10/10/2026",
                "total_amount": "1,250.50", "items": [{"description": "Consultation", "amount": "500"},
                                                      {"description": "Dressing", "amount": "750.50"}]}
        result = self.run_graph("medical_bill", bill)
        self.assertEqual(result["status"], "confirmed")
        rows = result["vault_record"]["confirmed_fields"]["items"]["value"]
        self.assertEqual([(row["description"], row["amount"]) for row in rows],
                         [("Consultation", 500.0), ("Dressing", 750.5)])
        without = self.run_graph("medical_bill", {key: value for key, value in bill.items() if key != "items"})
        self.assertEqual(without["status"], "confirmed")  # the items are not required

    def test_invalid_values_are_refused_with_reasons_and_no_full_number_is_left(self):
        wrong = str(int(DIGITS) + 1)  # fails the Aadhaar checksum
        cases = [
            ("identity", {**TYPED_IDENTITY, "aadhaar_number": wrong}, ["aadhaar_number: not valid"]),
            ("identity", {"name": "Meera Rao"}, ["date_of_birth: required"]),
            ("identity", {**TYPED_IDENTITY, "blood_group": "O+"}, ["blood_group: not a field of this document"]),
            ("identity", {**TYPED_IDENTITY, "name": {"first": "Meera"}}, ["name: must be text or a number",
                                                                         "name: required"]),
            ("identity", {**TYPED_IDENTITY, "name": True}, ["name: must be text or a number", "name: required"]),
            ("income_certificate", {"name": "Meera Rao", "annual_income": "1,20,000", "issue_date": "2027-01-01"},
             ["Issue date cannot be in the future"]),
            ("prescription", {**TYPED_PRESCRIPTION, "medicines": "Metformin"}, ["medicines: must be a list of rows",
                                                                                "medicines: required"]),
            ("prescription", {**TYPED_PRESCRIPTION, "medicines": [{"strength": "500 mg"}]},
             ["medicines row 1: no medicine name"]),
            ("medical_bill", {"name": "Meera Rao", "provider": "Demo Care Hospital", "bill_date": "10/10/2026",
                              "total_amount": "100", "items": [{"description": "Dressing", "amount": "abc"}]},
             ["items row 1: amount is not readable"]),
        ]
        for document_type, fields, errors in cases:
            with self.subTest(errors=errors):
                result = self.run_graph(document_type, fields)
                self.assertEqual(result["status"], "rejected")
                self.assertEqual(result["errors"], errors)
                self.assertIn("take_manual_entry:refused", result["events"])
                self.assertNotIn("vault_record", result)
                self.assertNotIn(wrong, json.dumps(result, default=str))
                self.assertNotIn(DIGITS, json.dumps(result, default=str))

    def test_blank_optional_fields_are_skipped(self):
        result = self.run_graph("identity", {**TYPED_IDENTITY, "pan_number": "", "aadhaar_number": None})
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(set(result["vault_record"]["confirmed_fields"]), {"name", "date_of_birth"})

    def test_a_type_must_be_chosen_and_no_file_given(self):
        for extra, error in (({"claimed_type": None}, "Choose one of"),
                             ({"claimed_type": "passport"}, "Choose one of"),
                             ({"document": {"owner_id": OWNER, "file_path": "card.png"}}, "has no file"),
                             ({"actor_id": OTHER_OWNER}, "owner does not match")):
            with self.subTest(error=error):
                state = {**manual_state("identity", TYPED_IDENTITY), **extra}
                with hash_key():
                    result = build_document_graph().invoke(state)
                self.assertEqual(result["status"], "rejected")
                self.assertTrue(any(error in message for message in result["errors"]), result["errors"])
                self.assertEqual(result["manual_fields"], {})
                self.assertNotIn(DIGITS, json.dumps(result, default=str))

    def test_the_same_card_already_in_the_vault_is_reported(self):
        digest = keyed_hash("aadhaar_number", DIGITS, FAKE_HASH_KEY)
        result = self.run_graph("identity", TYPED_IDENTITY, vault_identifier_hashes={digest: "doc-old"})
        self.assertIn("aadhaar_number already in the vault (document doc-old)", result["warnings"])

    def test_without_the_hash_key_nothing_is_published(self):
        with patch("backend.utils.privacy.find_env_file", return_value=None), \
                patch.dict("os.environ", {"SENIORLIFE_HASH_KEY": ""}):
            result = build_document_graph().invoke(manual_state("identity", TYPED_IDENTITY))
        self.assertEqual(result["status"], "failed_tool")
        self.assertNotIn(DIGITS, json.dumps(result, default=str))


class StorageTests(DatabaseCase):
    def create(self, document_type="identity", fields=None, owner=OWNER, **extra):
        with hash_key():
            return self.service.create_manual(owner, document_type, fields or TYPED_IDENTITY, **extra)

    def count(self, model):
        with self.engine.connect() as connection:
            return connection.execute(select(func.count()).select_from(model)).scalar_one()

    def test_a_typed_in_document_is_saved_confirmed_with_no_file(self):
        saved = self.create()
        self.assertEqual((saved["entry"], saved["status"], saved["version"]), ("manual", "confirmed", 1))
        self.assertEqual((saved["filename"], saved["mime_type"], saved["size_bytes"]), (None, None, None))
        self.assertFalse(saved["file_stored"])
        self.assertEqual(saved["fields"]["aadhaar_number"]["value"], "XXXX XXXX 7973")
        self.assertTrue(saved["fields"]["name"]["source_ref"].endswith(":entered_by_owner"))
        self.assertEqual(list(self.root.iterdir()), [])  # nothing written to the file store
        self.assertEqual(self.count(IdentifierHash), 2)
        self.assert_no_full_numbers(self.database_text())
        with self.engine.connect() as connection:
            detail = connection.execute(select(AuditEvent.detail)
                                        .where(AuditEvent.action == "document.manual_entry")).scalar_one()
        self.assertEqual(detail, {"fields": sorted(TYPED_IDENTITY), "version": 1})  # names, never values

    def test_typing_in_a_card_already_uploaded_is_reported(self):
        uploaded = self.upload(IDENTITY)
        saved = self.create()
        self.assertIn(f"aadhaar_number already in the vault (document {uploaded['document_id']})",
                      saved["warnings"])

    def test_invalid_input_is_refused_and_nothing_is_stored(self):
        cases = (("identity", {"name": "Meera Rao", "aadhaar_number": "1234"}),
                 ("passport", TYPED_IDENTITY), ("identity", {}))
        for document_type, fields in cases:
            with self.subTest(document_type=document_type, fields=fields):
                with hash_key(), self.assertRaises(ValidationException) as caught:
                    self.service.create_manual(OWNER, document_type, fields)
                self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(caught.exception.message, "Type in at least one field")
        with hash_key(), self.assertRaises(ValidationException) as caught:
            self.service.create_manual(OWNER, "identity", {"name": "Meera Rao", "aadhaar_number": "1234"})
        self.assertEqual(caught.exception.details,
                         {"errors": ["aadhaar_number: not valid", "date_of_birth: required"]})
        self.assertEqual(self.count(Document), 0)
        self.assertEqual(self.count(AuditEvent), 0)

    def test_without_the_hash_key_nothing_is_stored(self):
        with patch("backend.utils.privacy.find_env_file", return_value=None), \
                patch.dict("os.environ", {"SENIORLIFE_HASH_KEY": ""}), self.assertRaises(WorkflowFailed):
            self.service.create_manual(OWNER, "identity", TYPED_IDENTITY)
        self.assertEqual(self.count(Document), 0)

    def test_it_is_listed_and_read_only_by_its_owner(self):
        saved = self.create("prescription", TYPED_PRESCRIPTION)
        listed = self.service.list(OWNER, "prescription")
        self.assertEqual([(item["document_id"], item["entry"]) for item in listed], [(saved["document_id"], "manual")])
        self.assertEqual(self.service.list(OTHER_OWNER), [])


class RouteTests(ApiCase):
    def setUp(self):
        super().setUp()
        self.service = FakeService()
        self.service.create_manual = self.fake_create
        self.app.dependency_overrides[documents.get_document_service] = lambda: self.service

    def fake_create(self, *args, **kwargs):
        self.service.calls.append(("create_manual", args, kwargs))
        return {**DOCUMENT, "entry": "manual", "filename": None, "mime_type": None, "size_bytes": None,
                "file_stored": False}

    def test_the_typed_in_fields_are_passed_on(self):
        response = self.client.post("/api/v1/documents/manual", headers={"X-Actor-Id": OWNER}, json={
            "documentType": "identity", "fields": {"name": "Meera Rao"}, "ownerName": "Meera Rao"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.service.calls[-1], ("create_manual", (OWNER, "identity", {"name": "Meera Rao"}),
                                                  {"owner_name": "Meera Rao"}))
        data = response.json()["data"]
        self.assertEqual((data["entry"], data["filename"], data["fileStored"]), ("manual", None, False))
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_a_body_without_type_or_fields_is_refused(self):
        for body in ({"fields": {"name": "x"}}, {"documentType": "identity"}, {"documentType": "identity",
                                                                               "fields": "name"}):
            with self.subTest(body=body):
                response = self.client.post("/api/v1/documents/manual", headers={"X-Actor-Id": OWNER}, json=body)
                self.assertEqual(response.status_code, 422)
        self.assertEqual(self.service.calls, [])

    def test_the_actor_header_is_needed(self):
        response = self.client.post("/api/v1/documents/manual", json={"documentType": "identity", "fields": {}})
        self.assertEqual(response.status_code, 401)


class EndToEndTests(DatabaseCase, ApiCase):
    def setUp(self):
        DatabaseCase.setUp(self)
        ApiCase.setUp(self)
        self.app.dependency_overrides[documents.get_document_service] = lambda: self.service

    def post(self, body):
        with hash_key():
            return self.client.post("/api/v1/documents/manual", headers={"X-Actor-Id": OWNER}, json=body)

    def test_type_in_then_read(self):
        created = self.post({"documentType": "identity", "fields": TYPED_IDENTITY})
        self.assertEqual(created.status_code, 201)
        self.assertNotIn(DIGITS, created.text)
        document_id = created.json()["data"]["documentId"]
        read = self.client.get(f"/api/v1/documents/{document_id}", headers={"X-Actor-Id": OWNER}).json()["data"]
        self.assertEqual((read["entry"], read["status"]), ("manual", "confirmed"))
        self.assertEqual(read["fields"]["pan_number"]["value"], "XXXXXX814K")

    def test_errors_come_back_as_422_with_reasons_and_no_values(self):
        wrong = str(int(DIGITS) + 1)
        response = self.post({"documentType": "identity",
                              "fields": {**TYPED_IDENTITY, "aadhaar_number": wrong}})
        self.assertEqual(response.status_code, 422)
        self.assertIn("aadhaar_number: not valid", response.text)
        self.assertNotIn(wrong, response.text)


if __name__ == "__main__":
    unittest.main()
