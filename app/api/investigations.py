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
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field, model_validator

from app.api.investigation_access import approval_key, investigation_owner, owner_context
from app.config import get_settings
from app.domain.contract import FIXTURE_CONTRACT, hash_contract
from app.domain.types import BudgetConfig
from app.domain.validator import validate_input
from app.orchestrator import admission
from app.orchestrator.budget import BudgetExceeded, BudgetLedger, BudgetRunner
from app.orchestrator.executor import PairedRunRequest, PairedRunResult, run_pair
from app.orchestrator.investigation import InvestigationRunner
from app.providers.nebius import NebiusClient
from app.providers.runner import LocalRunnerAdapter, RunnerAdapter, WorkerHttpRunnerAdapter
from app.storage.investigation_store import InvestigationStore, make_investigation_store

router = APIRouter(prefix="/api", dependencies=[Depends(investigation_owner)])

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
# In-memory cache for active investigation work; the store is authoritative.
# ---------------------------------------------------------------------------

# { investigation_id_str: InvestigationRecord (metadata) }
_investigations: dict[str, dict[str, Any]] = {}

# { investigation_id_str: {"status": str, "progress": SearchProgress|None, "evidence": InvestigationEvidence|None} }
_investigation_state: dict[str, dict[str, Any]] = {}

# Created lazily after settings have loaded; tests can replace it.
_store: InvestigationStore | None = None


def get_store() -> InvestigationStore:
    """Return the configured investigation store (injectable for tests)."""
    global _store
    if _store is None:
        _store = make_investigation_store()
    return _store


def set_store(store: InvestigationStore | None) -> None:
    """Replace the module-level store (used in tests)."""
    global _store
    _store = store


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
    model_mode: Literal["configured", "mock"] = "configured"
    search_strategy: Literal["sweep", "random"] = "sweep"
    search_seed: int = Field(default=42, ge=0, le=2**32-1)
    max_candidates: int = Field(default=50, ge=1, le=1000)
    max_runs: int = Field(default=400, ge=1, le=4000)

    max_wall_seconds: int = Field(default=600, ge=1, le=1800)
    max_llm_calls: int = Field(default=2, ge=1, le=4)
    max_model_tokens: int = Field(default=24000, ge=1, le=48000)
    max_cost_usd: float = Field(default=0.1, gt=0, le=1)

    @model_validator(mode="after")
    def consistent_budget(self):
        if self.max_runs < self.max_candidates:
            raise ValueError("max_runs must be at least max_candidates")
        return self


class RunPairRequest(BaseModel):
    input_json: dict


class VerifyPatchRequest(BaseModel):
    diff_text: str = Field(max_length=100_000)
    description: str = ""
    rationale: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash_input(input_json: dict) -> str:
    return hashlib.sha256(json.dumps(input_json, sort_keys=True).encode()).hexdigest()


def _ensure_demo_project(project_id: uuid.UUID) -> None:
    if project_id != DEMO_PROJECT_ID:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")


def _get_investigation_or_404(investigation_id: str) -> dict[str, Any]:
    try:
        uuid.UUID(investigation_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Investigation not found") from exc
    stored = get_store().get_investigation(investigation_id)
    if stored is None:
        raise HTTPException(
            status_code=404,
            detail=f"Investigation {investigation_id} not found",
        )
    owner = owner_context.get()
    if owner is not None and stored.get("owner_id") != owner:
        raise HTTPException(status_code=404, detail="Investigation not found")
    record = _investigations.get(investigation_id)
    if record is None:
        budget = stored.get("budget_json", {})
        record = {
            "id": investigation_id,
            "project_id": stored["project_id"],
            "description": "",
            "owner_id": stored.get("owner_id"),
            "created_at": stored.get("created_at"),
            "runs": [],
            "max_candidates": budget.get("max_candidates", 50),
            "max_runs": budget.get("max_runs", 200),
        }
        _investigations[investigation_id] = record
    record["status"] = stored["status"]
    return record


def _budget_runner(investigation_id: str, runner: RunnerAdapter) -> BudgetRunner:
    saved = get_store().get_investigation(investigation_id)
    limits = {key: value for key, value in saved["budget_json"].items() if key != "usage"}
    ledger = BudgetLedger(BudgetConfig(**limits), get_store(), investigation_id)
    return BudgetRunner(runner, ledger)


# ---------------------------------------------------------------------------
# Background task helper
# ---------------------------------------------------------------------------


def _run_investigation_sync(
    investigation_id: str,
    runner: RunnerAdapter,
    store: InvestigationStore,
) -> None:
    """Synchronous wrapper executed in a thread via asyncio.to_thread."""
    state = _investigation_state.setdefault(investigation_id, {})
    state["status"] = "running"
    state["progress"] = None
    state["evidence"] = None
    try:
        saved = store.get_investigation(investigation_id)
        budget_values = saved.get("budget_json", {}) if saved else {}
        budget = BudgetConfig(
            **{key: value for key, value in budget_values.items() if key != "usage"}
        )
        guest_client = None
        if saved and (str(saved.get("owner_id", "")).startswith("guest:") or budget_values.get("model_mode") == "mock"):
            settings = get_settings()
            guest_client = NebiusClient(
                api_key=None,
                model_id=settings.nebius_model_id,
                api_base=settings.nebius_base_url,
            )
        inv_runner = InvestigationRunner(
            contract=FIXTURE_CONTRACT,
            budget=budget,
            runner=runner,
            nebius_client=guest_client,
            store=store,
        )
        evidence = inv_runner.run_full_investigation(investigation_id)
        from app.orchestrator.report import generate_report, generate_text_summary

        store.store_evidence(investigation_id, evidence.model_dump(mode="json"))
        report = generate_report(evidence)
        summary = generate_text_summary(evidence)
        bindings = [event["payload"] for event in store.get_events(investigation_id)
                    if event["event_type"] == "source_binding"]
        if bindings:
            report["source_binding"] = bindings[0]
            summary += "\nConnected source scope: " + json.dumps(bindings[0], sort_keys=True)
        store.store_report(investigation_id, report, summary)
        store.transition_status(investigation_id, "completed")
        state["evidence"] = evidence
        state["status"] = "completed"
        _investigations[investigation_id]["status"] = "completed"
    except Exception as exc:  # noqa: BLE001
        state["status"] = "failed"
        state["error"] = str(exc)
        _investigations[investigation_id]["status"] = "failed"
        try:
            store.append_event(
                investigation_id,
                "runner",
                "budget_exhausted" if isinstance(exc, BudgetExceeded) else "investigation_failed",
                {"error": str(exc)},
            )
            store.transition_status(investigation_id, "failed")
        except Exception:  # noqa: BLE001
            pass


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
    approval = get_store().get_approval(approval_key(str(project_id))) or {}
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
    approval = get_store().approve_contract(
        approval_key(str(project_id)), hash_contract(FIXTURE_CONTRACT)
    )
    return {
        "project_id": str(project_id),
        "approved": True,
        "contract_hash": approval["contract_hash"],
    }


@router.post("/investigations", status_code=201)
async def create_investigation(body: CreateInvestigationRequest) -> dict:
    """Create a new investigation for a project."""
    _ensure_demo_project(body.project_id)
    store = get_store()
    budget_json = BudgetConfig(
        **body.model_dump(exclude={"project_id", "description", "search_strategy", "search_seed", "model_mode"})
    ).model_dump()
    budget_json.update(search_strategy=body.search_strategy, search_seed=body.search_seed, model_mode=body.model_mode)
    inv_id = admission.create(store,
        project_id=str(body.project_id),
        budget_json=budget_json,
        owner_id=owner_context.get() or "user",
    )
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
    approval = get_store().get_approval(approval_key(record["project_id"])) or {}
    if approval.get("contract_hash") != hash_contract(FIXTURE_CONTRACT):
        raise HTTPException(status_code=409, detail="Approve the project contract before starting")
    if record["status"] != "draft":
        raise HTTPException(status_code=409, detail="Investigation has already been started")
    if isinstance(runner, WorkerHttpRunnerAdapter):
        from app.api.health import ready
        response = await ready()
        if response.status_code != 200:
            get_store().append_event(investigation_id, "runner", "stage_failed", {
                "stage": "preflight", "error": "Pinned worker readiness failed; no executions started",
            })
            raise HTTPException(503, "Pinned worker readiness failed; check /ready before retrying")

    state = _investigation_state.setdefault(investigation_id, {})
    store = get_store()
    try:
        admission.start(store, investigation_id, record.get("owner_id", owner_context.get() or "user"))
    except ValueError as exc:
        raise HTTPException(409, "Investigation has already been started") from exc
    state["status"] = "running"
    _investigations[investigation_id]["status"] = "running"

    asyncio.create_task(asyncio.to_thread(_run_investigation_sync, investigation_id, runner, store))

    return {"investigation_id": investigation_id, "status": "running"}


@router.get("/investigations/{investigation_id}/progress")
async def get_progress(investigation_id: str) -> dict:
    """Return the current investigation progress (status + events)."""
    record = _get_investigation_or_404(investigation_id)
    state = _investigation_state.get(investigation_id, {})
    status = record["status"]

    result: dict[str, Any] = {
        "investigation_id": investigation_id,
        "status": status,
    }

    evidence = state.get("evidence")
    if evidence is not None:
        result["witness_found"] = evidence.witness is not None
        result["verdict"] = evidence.verdict.value if evidence.verdict else None
    else:
        saved_report = get_store().get_report(investigation_id) if status == "completed" else None
        result["witness_found"] = saved_report.get("witness") is not None if saved_report else False
        result["verdict"] = saved_report.get("verdict") if saved_report else None

    # Include events from the store
    store = get_store()
    events = store.get_events(investigation_id)
    result["events"] = events
    result["event_count"] = len(events)
    error_event = next(
        (event for event in reversed(events) if event.get("event_type") == "investigation_failed"),
        None,
    )
    if not error_event:
        error_event = next((event for event in reversed(events) if event.get("event_type") == "budget_exhausted"), None)
    if state.get("error") or error_event:
        result["error"] = state.get("error") or error_event.get("payload", {}).get("error")

    return result


@router.get("/investigations/{investigation_id}/evidence")
async def get_evidence(investigation_id: str) -> dict:
    """Return the full InvestigationEvidence once the investigation is complete."""
    record = _get_investigation_or_404(investigation_id)
    if record["status"] != "completed":
        raise HTTPException(status_code=404, detail="Evidence not yet available")
    state = _investigation_state.get(investigation_id, {})
    evidence = state.get("evidence")
    if evidence is None:
        persisted = get_store().get_evidence(investigation_id)
        if persisted is not None:
            return persisted
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

    record = _get_investigation_or_404(investigation_id)
    if record["status"] != "completed":
        raise HTTPException(status_code=404, detail="Report not yet available")
    state = _investigation_state.get(investigation_id, {})
    evidence = state.get("evidence")

    # Try store first (persisted across restarts)
    store = get_store()
    persisted = store.get_report(investigation_id)
    if persisted is not None:
        return persisted

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

    record = _get_investigation_or_404(investigation_id)
    if record["status"] != "completed":
        raise HTTPException(status_code=404, detail="Report not yet available")
    state = _investigation_state.get(investigation_id, {})
    evidence = state.get("evidence")
    persisted = get_store().get_report_text(investigation_id)
    if persisted is not None:
        return PlainTextResponse(persisted)
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

        store = get_store()
        last_seq = -1

        while True:
            state = _investigation_state.get(investigation_id, {})
            status = _get_investigation_or_404(investigation_id)["status"]
            evidence = state.get("evidence")

            payload: dict[str, Any] = {"status": status}
            if evidence is not None:
                payload["witness_found"] = evidence.witness is not None
                payload["verdict"] = evidence.verdict.value if evidence.verdict else None
            if state.get("error"):
                payload["error"] = state["error"]

            # Emit any new store events
            events = store.get_events(investigation_id)
            new_events = [e for e in events if e.get("sequence", -1) > last_seq]
            if new_events:
                last_seq = new_events[-1].get("sequence", last_seq)
                payload["new_events"] = new_events

            yield f"data: {_json.dumps(payload)}\n\n"

            if status in ("completed", "failed", "interrupted", "cancelled"):
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
    if investigation_id == "demo":
        raise HTTPException(409, "Create an owned, budgeted investigation before running a pair")
    _get_investigation_or_404(investigation_id)

    if set(body.input_json) != {"signal"}:
        raise HTTPException(422, "Only the approved signal input is accepted")
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
    try:
        result: PairedRunResult = await asyncio.to_thread(
            run_pair, request, _budget_runner(investigation_id, runner)
        )
    except BudgetExceeded as exc:
        raise HTTPException(429, str(exc)) from exc

    result_dict = result.model_dump(mode="json")

    # Record in investigation if not demo
    if investigation_id in _investigations:
        _investigations[investigation_id]["runs"].append(result_dict)
        get_store().append_event(
            investigation_id,
            actor="api",
            event_type="paired_run_completed",
            payload={
                "candidate_sha256": candidate_sha256,
                "old_status": result.old_run.status,
                "new_status": result.new_run.status,
                "conclusion_changed": result.conclusion_changed,
            },
        )

    return result_dict


@router.post("/investigations/{investigation_id}/verify-patch")
async def verify_patch_endpoint(
    investigation_id: str,
    body: VerifyPatchRequest,
    runner: RunnerAdapter = Depends(get_runner),  # noqa: B008
) -> dict:
    """Execute a constrained diff against the completed investigation's frozen cases."""
    from app.orchestrator.patch_verifier import ProposedPatch, apply_and_verify_patch

    _get_investigation_or_404(investigation_id)

    proposal = ProposedPatch(
        diff_text=body.diff_text,
        description=body.description,
        rationale=body.rationale,
    )

    from app.domain.contract import ContractBody

    if _get_investigation_or_404(investigation_id)["status"] != "completed":
        raise HTTPException(409, "Complete the investigation before verifying a repair")
    evidence = get_store().get_evidence(investigation_id)
    if not evidence or not evidence.get("witness"):
        raise HTTPException(
            status_code=409, detail="A completed investigation with a witness is required"
        )
    approved_contract = ContractBody.model_validate(evidence["contract_body"])
    if hash_contract(approved_contract) != evidence["contract_hash"]:
        raise HTTPException(status_code=409, detail="Stored contract hash mismatch")
    result = await asyncio.to_thread(
        apply_and_verify_patch,
        proposal,
        evidence.get("frozen_holdouts"),
        evidence["witness"]["input_json"],
        approved_contract,
        _budget_runner(investigation_id, runner),
    )

    # Record as an event
    store = get_store()
    store.append_event(
        investigation_id,
        actor="api",
        event_type="patch_verification_requested",
        payload={
            "verdict": result.verdict,
            "parse_valid": result.parse_result.valid,
            "description": body.description,
            "verification": result.model_dump(mode="json"),
        },
    )

    return result.model_dump(mode="json")
