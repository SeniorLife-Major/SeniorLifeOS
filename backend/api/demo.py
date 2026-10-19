"""Demo Mode Controller API.

Exposes pre-seeded test scenarios allowing mentors, evaluators, and frontend developers
to execute and verify workflows with a single click and zero manual configuration.
"""

from __future__ import annotations

from typing import Any
from fastapi import APIRouter, status

from backend.common.schemas import ApiResponse, CamelModel
from backend.core.config import get_config
from backend.demo.demo_cases import (
    benefits_failure_state,
    benefits_success_state,
    document_failure_state,
    document_success_state,
)
from backend.errors import NotFoundException
from backend.graphs.benefits_graph import build_benefits_graph
from backend.graphs.document_graph import build_document_graph

router = APIRouter(prefix="/api/v1/demo", tags=["Demo Mode"])


class DemoScenarioDto(CamelModel):
    scenario_id: str
    title: str
    workflow_type: str
    description: str
    expected_status: str


AVAILABLE_SCENARIOS = [
    DemoScenarioDto(
        scenario_id="doc-income-success",
        title="Document Intake: Income Certificate (Success)",
        workflow_type="document_intake",
        description="Processes an income certificate with a malformed date, exercises human correction, and publishes a confirmed vault record.",
        expected_status="confirmed",
    ),
    DemoScenarioDto(
        scenario_id="doc-blurry-reupload",
        title="Document Intake: Blurry Upload (Edge Case)",
        workflow_type="document_intake",
        description="Low image quality (0.25) fails confidence threshold and routes to needs_reupload without publishing.",
        expected_status="needs_reupload",
    ),
    DemoScenarioDto(
        scenario_id="benefits-pension-success",
        title="Benefits Case: Pension Application Package (Success)",
        workflow_type="benefits_case",
        description="Assesses eligibility for Demo Senior Support Pension using confirmed identity, residence, and income records. Creates approved handoff package.",
        expected_status="ready_for_manual_handoff",
    ),
    DemoScenarioDto(
        scenario_id="benefits-missing-evidence",
        title="Benefits Case: Missing Income Document (Edge Case)",
        workflow_type="benefits_case",
        description="Evaluates pension without an income certificate; marks criterion as unknown and returns needs_input with missing checklist.",
        expected_status="needs_input",
    ),
]


@router.get("/scenarios", response_model=ApiResponse[list[DemoScenarioDto]])
def list_demo_scenarios() -> ApiResponse[list[DemoScenarioDto]]:
    """List all pre-configured demo scenarios available for one-click testing."""
    return ApiResponse.of(data=AVAILABLE_SCENARIOS, version=get_config().app.version)


@router.post("/scenarios/{scenario_id}/execute", response_model=ApiResponse[dict[str, Any]])
def execute_demo_scenario(scenario_id: str) -> ApiResponse[dict[str, Any]]:
    """Execute a pre-configured demo scenario and return its execution trace and artifacts."""
    config = get_config()

    if scenario_id == "doc-income-success":
        result = build_document_graph().invoke(document_success_state())
        return ApiResponse.of(data=result, version=config.app.version)

    if scenario_id == "doc-blurry-reupload":
        result = build_document_graph().invoke(document_failure_state())
        return ApiResponse.of(data=result, version=config.app.version)

    if scenario_id == "benefits-pension-success":
        doc_result = build_document_graph().invoke(document_success_state())
        income_vault_record = doc_result["vault_record"]
        ben_result = build_benefits_graph().invoke(benefits_success_state(income_vault_record))
        return ApiResponse.of(data=ben_result, version=config.app.version)

    if scenario_id == "benefits-missing-evidence":
        result = build_benefits_graph().invoke(benefits_failure_state())
        return ApiResponse.of(data=result, version=config.app.version)

    raise NotFoundException(
        message=f"Demo scenario '{scenario_id}' not found. Available scenarios: {[s.scenario_id for s in AVAILABLE_SCENARIOS]}",
        details={"scenarioId": scenario_id},
    )
