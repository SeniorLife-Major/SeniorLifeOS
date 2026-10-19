"""Unit tests for the standardized API response envelopes and global error handler."""

import unittest
from fastapi import FastAPI, APIRouter
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from backend.common.schemas import ApiResponse, ApiErrorResponse, CamelModel
from backend.errors import (
    AppException,
    BadRequestException,
    CaseNotFoundException,
    CaseVersionConflictException,
    ErrorCode,
    register_global_error_handlers,
)


class SampleUserDto(CamelModel):
    user_id: str
    first_name: str
    contact_email: str | None = None


class CreateCaseRequest(CamelModel):
    case_name: str = Field(min_length=3)
    actor_id: str


class ResponseEnvelopeTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        register_global_error_handlers(self.app)

        router = APIRouter(prefix="/api/v1/test")

        @router.get("/single-item")
        def get_single_item():
            item = SampleUserDto(user_id="usr-101", first_name="Meera", contact_email="meera@demo.org")
            return ApiResponse.of(data=item)

        @router.get("/list-items")
        def get_list_items():
            items = [
                SampleUserDto(user_id="usr-101", first_name="Meera"),
                SampleUserDto(user_id="usr-102", first_name="Ramesh"),
            ]
            return ApiResponse.of(data=items)

        @router.get("/case-not-found")
        def raise_case_not_found():
            raise CaseNotFoundException(case_id="case-999")

        @router.get("/version-conflict")
        def raise_version_conflict():
            raise CaseVersionConflictException(case_id="case-999", current_version=2, expected_version=1)

        @router.get("/generic-bad-request")
        def raise_bad_request():
            raise BadRequestException("Invalid parameter value", details={"param": "page_number"})

        @router.post("/validate-body")
        def validate_body(req: CreateCaseRequest):
            return ApiResponse.of(data={"caseName": req.case_name, "actorId": req.actor_id})

        @router.get("/unhandled-error")
        def raise_unhandled():
            raise RuntimeError("Database connection suddenly dropped")

        self.app.include_router(router)
        self.client = TestClient(self.app, raise_server_exceptions=False)

    def test_single_item_success_envelope_has_camel_case(self):
        resp = self.client.get("/api/v1/test/single-item")
        self.assertEqual(resp.status_code, 200)
        json_data = resp.json()

        self.assertEqual(json_data["status"], "SUCCESS")
        self.assertIn("data", json_data)
        self.assertIn("meta", json_data)

        # Assert camelCase serialization
        self.assertEqual(json_data["data"]["userId"], "usr-101")
        self.assertEqual(json_data["data"]["firstName"], "Meera")
        self.assertEqual(json_data["data"]["contactEmail"], "meera@demo.org")
        self.assertIn("serverTime", json_data["meta"])
        self.assertIn("version", json_data["meta"])

    def test_list_items_success_envelope(self):
        resp = self.client.get("/api/v1/test/list-items")
        self.assertEqual(resp.status_code, 200)
        json_data = resp.json()

        self.assertEqual(json_data["status"], "SUCCESS")
        self.assertIsInstance(json_data["data"], list)
        self.assertEqual(len(json_data["data"]), 2)
        self.assertEqual(json_data["data"][0]["userId"], "usr-101")

    def test_case_not_found_renders_404_error_envelope(self):
        resp = self.client.get("/api/v1/test/case-not-found")
        self.assertEqual(resp.status_code, 404)
        json_data = resp.json()

        self.assertEqual(json_data["status"], "ERROR")
        self.assertEqual(json_data["requestUri"], "/api/v1/test/case-not-found")
        self.assertEqual(json_data["error"]["code"], ErrorCode.CASE_NOT_FOUND.value)
        self.assertIn("case-999", json_data["error"]["message"])
        self.assertEqual(json_data["error"]["details"]["caseId"], "case-999")
        self.assertIn("serverTime", json_data["meta"])

    def test_version_conflict_renders_409_error_envelope(self):
        resp = self.client.get("/api/v1/test/version-conflict")
        self.assertEqual(resp.status_code, 409)
        json_data = resp.json()

        self.assertEqual(json_data["status"], "ERROR")
        self.assertEqual(json_data["error"]["code"], ErrorCode.CASE_VERSION_CONFLICT.value)
        self.assertEqual(json_data["error"]["details"]["currentVersion"], 2)
        self.assertEqual(json_data["error"]["details"]["expectedVersion"], 1)

    def test_pydantic_validation_error_renders_422_with_field_details(self):
        resp = self.client.post("/api/v1/test/validate-body", json={"caseName": "ab"})  # too short & missing actor_id
        self.assertEqual(resp.status_code, 422)
        json_data = resp.json()

        self.assertEqual(json_data["status"], "ERROR")
        self.assertEqual(json_data["error"]["code"], ErrorCode.VALIDATION_FAILED.value)
        self.assertIsInstance(json_data["error"]["details"], list)
        self.assertTrue(len(json_data["error"]["details"]) >= 2)

    def test_unhandled_error_renders_500_safe_envelope(self):
        resp = self.client.get("/api/v1/test/unhandled-error")
        self.assertEqual(resp.status_code, 500)
        json_data = resp.json()

        self.assertEqual(json_data["status"], "ERROR")
        self.assertEqual(json_data["error"]["code"], ErrorCode.INTERNAL_SERVER_ERROR.value)
        self.assertIn("serverTime", json_data["meta"])


if __name__ == "__main__":
    unittest.main()
