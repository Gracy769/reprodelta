"""
API endpoints for investigations and projects.

Routes:
  GET  /api/projects                              → list demo project
  GET  /api/projects/{project_id}/contract        → get the fixture contract
  POST /api/projects/{project_id}/contract/approve → approve the contract
  POST /api/investigations                        → create a new investigation
  GET  /api/investigations/{id}                   → get investigation status
  POST /api/investigations/{id}/run-pair          → run a paired execution
  POST /api/investigations/{id}/start             → start full investigation loop
  GET  /api/investigations/{id}/progress          → return current SearchProgress
  GET  /api/investigations/{id}/evidence          → return InvestigationEvidence (once complete)
  GET  /api/investigations/{id}/report            → return JSON evidence bundle
  GET  /api/investigations/{id}/report/text       → return text summary
  GET  /api/investigations/{id}/stream            → Server-Sent Events progress stream
"""
from __future__ import annotations

import asyncio
import asyncio as _asyncio
import hashlib
import json
import pathlib
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.config import get_settings
from app.domain.contract import FIXTURE_CONTRACT, hash_contract
from app.domain.types import BudgetConfig
from app.domain.validator import validate_input
from app.orchestrator.executor import PairedRunRequest, PairedRunResult, run_pair
from app.orchestrator.investigation import InvestigationRunner
from app.providers.runner import LocalRunnerAdapter, RunnerAdapter, WorkerHttpRunnerAdapter

router = APIRouter(prefix="/api")

# ---------------------------------------------------------------------------
# Fixed demo project
# ---------------------------------------------------------------------------

DEMO_PROJECT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

_DEMO_PROJECT = {
    "id": str(DEMO_PROJECT_ID),
    "name": "Signal Classifier (NumPy 1.26 → 2.0 demo)",
    "description": (
        "Curated fixture demonstrating a NumPy NEP-50 scalar promotion change "
        "that flips a classification near the threshold."
    ),
    "fixture": "fixture/project/pipeline.py",
}

# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------

# { project_id_str: {"approved": bool, "approved_at": datetime | None, "approved_by": str | None} }
_contract_approvals: dict[str, dict[str, Any]] = {}

# { investigation_id_str: InvestigationRecord }
_investigations: dict[str, dict[str, Any]] = {}

# { investigation_id_str: {"status": str, "progress": SearchProgress|None, "evidence": InvestigationEvidence|None} }
_investigation_state: dict[str, dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# Dependency injection — runner
# ---------------------------------------------------------------------------

def _default_fixture_dir() -> pathlib.Path:
    # Resolve relative to this file: ../../fixture/project
    here = pathlib.Path(__file__).parent.parent.parent
    return here / "fixture" / "project"


def get_runner() -> RunnerAdapter:
    settings = get_settings()
    if settings.runner_mode == "local":
        return LocalRunnerAdapter(fixture_dir=_default_fixture_dir())
    if settings.runner_mode == "worker_http":
        return WorkerHttpRunnerAdapter(settings.worker_old_url, settings.worker_new_url)
    raise ValueError(f"Unknown RUNNER_MODE: {settings.runner_mode}")


# ---------------------------------------------------------------------------
# Request/response schemas
# ---------------------------------------------------------------------------

class CreateInvestigationRequest(BaseModel):
    project_id: uuid.UUID
    description: str = ""
    max_candidates: int = Field(default=50, ge=1, le=1000)
    max_runs: int = Field(default=200, ge=1, le=4000)


class RunPairRequest(BaseModel):
    input_json: dict


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hash_input(input_json: dict) -> str:
    return hashlib.sha256(
        json.dumps(input_json, sort_keys=True).encode()
    ).hexdigest()


def _ensure_demo_project(project_id: uuid.UUID) -> None:
    if project_id != DEMO_PROJECT_ID:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")


def _get_investigation_or_404(investigation_id: str) -> dict[str, Any]:
    record = _investigations.get(investigation_id)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"Investigation {investigation_id} not found",
        )
    return record


# ---------------------------------------------------------------------------
# Background task helper
# ---------------------------------------------------------------------------

def _run_investigation_sync(investigation_id: str, runner: RunnerAdapter) -> None:
    """Synchronous wrapper executed in a thread via asyncio.to_thread."""
    state = _investigation_state.setdefault(investigation_id, {})
    state["status"] = "running"
    state["progress"] = None
    state["evidence"] = None
    try:
        budget = BudgetConfig(
            max_candidates=_investigations[investigation_id].get("max_candidates", 50),
            max_runs=_investigations[investigation_id].get("max_runs", 200),
        )
        inv_runner = InvestigationRunner(
            contract=FIXTURE_CONTRACT,
            budget=budget,
            runner=runner,
        )
        evidence = inv_runner.run_full_investigation(investigation_id)
        state["evidence"] = evidence
        state["status"] = "completed"
        # Expose search progress summary via evidence
        state["progress"] = None  # progress is embedded in evidence
        _investigations[investigation_id]["status"] = "completed"
    except Exception as exc:  # noqa: BLE001
        state["status"] = "failed"
        state["error"] = str(exc)
        _investigations[investigation_id]["status"] = "failed"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/projects")
async def list_projects() -> list[dict]:
    """Return the single demo project."""
    return [_DEMO_PROJECT]


@router.get("/projects/{project_id}/contract")
async def get_contract(project_id: uuid.UUID) -> dict:
    """Return the fixture contract for a project."""
    _ensure_demo_project(project_id)
    body = FIXTURE_CONTRACT.model_dump()
    contract_hash = hash_contract(FIXTURE_CONTRACT)
    approval = _contract_approvals.get(str(project_id), {})
    return {
        "project_id": str(project_id),
        "contract": body,
        "contract_hash": contract_hash,
        "approved": approval.get("approved", False),
        "approved_at": approval.get("approved_at"),
        "approved_by": approval.get("approved_by"),
    }


@router.post("/projects/{project_id}/contract/approve")
async def approve_contract(project_id: uuid.UUID) -> dict:
    """Record contract approval for a project."""
    _ensure_demo_project(project_id)
    _contract_approvals[str(project_id)] = {
        "approved": True,
        "approved_at": datetime.now(UTC).isoformat(),
        "approved_by": "user",
    }
    return {
        "project_id": str(project_id),
        "approved": True,
        "contract_hash": hash_contract(FIXTURE_CONTRACT),
    }


@router.post("/investigations", status_code=201)
async def create_investigation(body: CreateInvestigationRequest) -> dict:
    """Create a new (in-memory) investigation for a project."""
    _ensure_demo_project(body.project_id)
    inv_id = str(uuid.uuid4())
    record: dict[str, Any] = {
        "id": inv_id,
        "project_id": str(body.project_id),
        "description": body.description,
        "status": "draft",
        "created_at": datetime.now(UTC).isoformat(),
        "runs": [],
        "max_candidates": body.max_candidates,
        "max_runs": body.max_runs,
    }
    _investigations[inv_id] = record
    _investigation_state[inv_id] = {
        "status": "draft",
        "progress": None,
        "evidence": None,
    }
    return record


@router.get("/investigations/{investigation_id}")
async def get_investigation(investigation_id: str) -> dict:
    """Return an investigation record."""
    return _get_investigation_or_404(investigation_id)


@router.post("/investigations/{investigation_id}/start", status_code=202)
async def start_investigation(
    investigation_id: str,
    runner: RunnerAdapter = Depends(get_runner),  # noqa: B008
) -> dict:
    """
    Trigger the full investigation loop as a background task.

    Returns 202 Accepted immediately.  Poll GET .../progress for updates.
    """
    record = _get_investigation_or_404(investigation_id)
    if not _contract_approvals.get(record["project_id"], {}).get("approved"):
        raise HTTPException(status_code=409, detail="Approve the project contract before starting")
    state = _investigation_state.setdefault(investigation_id, {})

    if state.get("status") != "draft":
        raise HTTPException(status_code=409, detail="Investigation has already been started")

    state["status"] = "running"
    _investigations[investigation_id]["status"] = "running"

    asyncio.create_task(
        asyncio.to_thread(_run_investigation_sync, investigation_id, runner)
    )

    return {"investigation_id": investigation_id, "status": "running"}


@router.get("/investigations/{investigation_id}/progress")
async def get_progress(investigation_id: str) -> dict:
    """Return the current investigation progress (status + search counts)."""
    _get_investigation_or_404(investigation_id)
    state = _investigation_state.get(investigation_id, {})
    status = state.get("status", "draft")

    result: dict[str, Any] = {
        "investigation_id": investigation_id,
        "status": status,
    }

    evidence = state.get("evidence")
    if evidence is not None:
        # Build a lightweight progress summary from the completed evidence
        result["witness_found"] = evidence.witness is not None
        result["verdict"] = evidence.verdict.value if evidence.verdict else None
    else:
        result["witness_found"] = False
        result["verdict"] = None

    if state.get("error"):
        result["error"] = state["error"]

    return result


@router.get("/investigations/{investigation_id}/evidence")
async def get_evidence(investigation_id: str) -> dict:
    """Return the full InvestigationEvidence once the investigation is complete."""
    _get_investigation_or_404(investigation_id)
    state = _investigation_state.get(investigation_id, {})
    evidence = state.get("evidence")
    if evidence is None:
        status = state.get("status", "draft")
        raise HTTPException(
            status_code=404,
            detail=f"Evidence not yet available (status={status})",
        )
    return evidence.model_dump(mode="json")


@router.get("/investigations/{investigation_id}/report")
async def get_report(investigation_id: str) -> dict:
    """Return the full JSON evidence bundle for a completed investigation."""
    from app.orchestrator.report import generate_report

    _get_investigation_or_404(investigation_id)
    state = _investigation_state.get(investigation_id, {})
    evidence = state.get("evidence")
    if evidence is None:
        status = state.get("status", "draft")
        raise HTTPException(
            status_code=404,
            detail=f"Report not yet available (status={status})",
        )
    return generate_report(evidence)


@router.get("/investigations/{investigation_id}/report/text", response_class=PlainTextResponse)
async def get_report_text(investigation_id: str) -> PlainTextResponse:
    """Return the plain-text evidence summary for a completed investigation."""
    from app.orchestrator.report import generate_text_summary

    _get_investigation_or_404(investigation_id)
    state = _investigation_state.get(investigation_id, {})
    evidence = state.get("evidence")
    if evidence is None:
        status = state.get("status", "draft")
        raise HTTPException(
            status_code=404,
            detail=f"Report not yet available (status={status})",
        )
    return PlainTextResponse(generate_text_summary(evidence))


@router.get("/investigations/{investigation_id}/stream")
async def stream_progress(investigation_id: str) -> StreamingResponse:
    """Server-Sent Events endpoint — pushes progress until investigation completes."""
    _get_investigation_or_404(investigation_id)

    async def _event_generator():
        import json as _json

        while True:
            state = _investigation_state.get(investigation_id, {})
            status = state.get("status", "draft")
            evidence = state.get("evidence")

            payload: dict[str, Any] = {"status": status}
            if evidence is not None:
                payload["witness_found"] = evidence.witness is not None
                payload["verdict"] = evidence.verdict.value if evidence.verdict else None
            if state.get("error"):
                payload["error"] = state["error"]

            yield f"data: {_json.dumps(payload)}\n\n"

            if status in ("completed", "failed"):
                break
            await _asyncio.sleep(1)

    return StreamingResponse(_event_generator(), media_type="text/event-stream")


@router.post("/investigations/{investigation_id}/run-pair")
async def run_pair_endpoint(
    investigation_id: str,
    body: RunPairRequest,
    runner: RunnerAdapter = Depends(get_runner),  # noqa: B008
) -> dict:
    """Run a paired (old vs new) execution for a given input.

    Steps:
    1. Validate the input against the fixture contract.
    2. Hash the input.
    3. Submit paired runs via the runner adapter.
    4. Return the PairedRunResult.
    """
    # Allow "demo" as a convenience alias
    if investigation_id != "demo" and investigation_id not in _investigations:
        raise HTTPException(
            status_code=404,
            detail=f"Investigation {investigation_id} not found",
        )

    validation = validate_input(FIXTURE_CONTRACT, body.input_json)
    if not validation.valid:
        raise HTTPException(status_code=422, detail=validation.reason)

    candidate_sha256 = _hash_input(body.input_json)
    inv_id = DEMO_PROJECT_ID if investigation_id == "demo" else uuid.UUID(investigation_id)

    request = PairedRunRequest(
        investigation_id=inv_id,
        candidate=body.input_json,
        candidate_sha256=candidate_sha256,
        contract=FIXTURE_CONTRACT,
    )
    result: PairedRunResult = run_pair(request, runner)

    result_dict = result.model_dump(mode="json")

    # Record in investigation if not demo
    if investigation_id in _investigations:
        _investigations[investigation_id]["runs"].append(result_dict)
        _investigations[investigation_id]["status"] = "running"

    return result_dict
