"""Owner-scoped admission and status for approved-project investigations."""

from __future__ import annotations

import asyncio
import math
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.connections import (
    _github_repository,
    _gitlab_project,
    _host_get,
    _host_pages,
    _saved_token,
    current_user,
)
from app.api.source_analysis import analyze, change_refs, immutable_snapshot
from app.config import get_settings
from app.domain.project_eligibility import check_eligibility
from app.domain.project_profile import ProfileApproval, ProjectProfileDraft
from app.domain.project_run import ProjectRunSpec
from app.storage.project_profiles import ProjectProfileStore
from app.storage.project_run_queue import ProjectRunQueue

router = APIRouter(prefix="/api/project-runs", tags=["project runs"])


class StartProjectRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: uuid.UUID
    change_number: int = Field(ge=1, le=10_000_000)
    expected_base_sha: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    expected_head_sha: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    approved_contract_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved_cost_ceiling_usd: float = Field(gt=0, le=100)
    source_transfer_approved: bool
    idempotency_key: str = Field(min_length=8, max_length=128)


def get_run_queue() -> ProjectRunQueue:
    return ProjectRunQueue(get_settings().database_url)


def get_profile_store() -> ProjectProfileStore:
    return ProjectProfileStore(get_settings().database_url)


def _public_job(job: dict) -> dict:
    # Lease capabilities and idempotency keys are worker-side details.
    return {
        key: value for key, value in job.items()
        if key not in {"owner_id", "lease_token", "lease_expires_at", "idempotency_key"}
    }


async def _plan(
    owner_id: str,
    profile_id: uuid.UUID,
    change_number: int,
    profiles: ProjectProfileStore,
    *,
    snapshot: bool,
) -> tuple[dict, ProjectRunSpec | None]:
    profile = await asyncio.to_thread(profiles.get, owner_id, profile_id)
    if profile is None:
        raise HTTPException(404, "Profile not found")
    if profile["status"] != "approved" or not profile.get("approval_json"):
        raise HTTPException(409, "This project profile is not approved for execution")
    try:
        draft = ProjectProfileDraft.model_validate(profile["draft_json"])
        approval = ProfileApproval.model_validate(profile["approval_json"])
    except ValueError as exc:
        raise HTTPException(409, "Approved project profile is invalid") from exc
    if (
        draft.digest != profile["profile_digest"]
        or draft.contract_hash != profile["contract_hash"]
        or approval.profile_digest != draft.digest
        or not draft.source_transfer_consent
    ):
        raise HTTPException(409, "Approved project profile changed or lacks transfer consent")
    provider, repository = profile["provider"], profile["repository"]
    source_id = _github_repository(repository) if provider == "github" else _gitlab_project(repository)
    token = await asyncio.to_thread(_saved_token, owner_id, provider)
    review = await analyze(
        _host_get, _host_pages, provider, token, source_id, repository, change_number
    )
    eligibility = check_eligibility(profile, review)
    settings = get_settings()
    rate = settings.e2b_sandbox_usd_per_second
    ceiling = settings.e2b_project_run_cost_ceiling_usd
    rate_ready = math.isfinite(rate) and math.isfinite(ceiling) and rate > 0 and ceiling > 0
    max_sandbox_seconds = min(1800, math.floor(ceiling / rate)) if rate_ready else 0
    # Two version checks and two bounded adapter calls need enough room to
    # complete; a smaller ceiling is not an executable plan.
    if max_sandbox_seconds < 120:
        rate_ready = False
        max_sandbox_seconds = 0
    plan = {
        **eligibility,
        "repository": repository,
        "provider": provider,
        "change_number": change_number,
        "profile_digest": draft.digest,
        "contract_hash": draft.contract_hash,
        "old_environment": approval.old.model_dump(mode="json"),
        "new_environment": approval.new.model_dump(mode="json"),
        "source_scope": "same immutable base source snapshot in both prepared NumPy environments",
        "source_transfer_notice": (
            "The selected source snapshot is sent to E2B for isolated execution. "
            "Review the provider's retention and deletion policy before approving."
        ),
        "max_wall_seconds": 900,
        "max_sandbox_seconds": max_sandbox_seconds,
        "estimated_max_sandbox_cost_usd": round(max_sandbox_seconds * rate, 6)
        if rate_ready else None,
        "cost_ceiling_usd": ceiling if rate_ready else None,
        "rate_configured": rate_ready,
        "execution_enabled": settings.e2b_project_runs_enabled,
    }
    if not eligibility["eligible"] or not snapshot or not rate_ready:
        return plan, None
    _, source_digest = await immutable_snapshot(
        _host_get, provider, token, source_id, review["base_sha"]
    )
    if await change_refs(_host_get, provider, token, source_id, change_number) != (
        review["base_sha"], review["head_sha"]
    ):
        raise HTTPException(409, "PR/MR changed during admission; review the latest revision")
    plan["source_sha256"] = source_digest
    spec = ProjectRunSpec(
        profile_id=profile_id,
        provider=provider,
        repository=repository,
        change_number=change_number,
        base_sha=review["base_sha"],
        head_sha=review["head_sha"],
        source_sha256=source_digest,
        profile_digest=draft.digest,
        contract_hash=draft.contract_hash,
        old_environment_lock_sha256=approval.old.lock_sha256,
        new_environment_lock_sha256=approval.new.lock_sha256,
        max_sandbox_seconds=max_sandbox_seconds,
        sandbox_usd_per_second=rate,
        max_cost_usd=ceiling,
    )
    return plan, spec


@router.get("/profiles/{profile_id}/changes/{change_number}/plan")
async def run_plan(
    profile_id: uuid.UUID,
    change_number: int,
    owner_id: Annotated[str, Depends(current_user)],
    profiles: Annotated[ProjectProfileStore, Depends(get_profile_store)],
):
    if not 1 <= change_number <= 10_000_000:
        raise HTTPException(422, "Invalid change number")
    plan, _ = await _plan(owner_id, profile_id, change_number, profiles, snapshot=False)
    return plan


@router.post("", status_code=202)
async def start_run(
    body: StartProjectRun,
    owner_id: Annotated[str, Depends(current_user)],
    profiles: Annotated[ProjectProfileStore, Depends(get_profile_store)],
    queue: Annotated[ProjectRunQueue, Depends(get_run_queue)],
):
    if not get_settings().e2b_project_runs_enabled:
        raise HTTPException(503, "Approved-project execution is not enabled")
    if not body.source_transfer_approved:
        raise HTTPException(422, "Approve source transfer to E2B before starting")
    plan, spec = await _plan(
        owner_id, body.profile_id, body.change_number, profiles, snapshot=True
    )
    if not plan["rate_configured"]:
        raise HTTPException(503, "A verified sandbox rate and cost ceiling are required")
    if spec is None:
        raise HTTPException(409, {"mode": plan["mode"], "reasons": plan["reasons"]})
    if (
        body.expected_base_sha != spec.base_sha
        or body.expected_head_sha != spec.head_sha
        or body.approved_contract_hash != spec.contract_hash
        or body.approved_cost_ceiling_usd != spec.max_cost_usd
    ):
        raise HTTPException(409, "Run plan changed; review the contract, commits and cost again")
    try:
        job = await asyncio.to_thread(queue.submit, owner_id, body.idempotency_key, spec)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _public_job(job)


@router.get("")
async def list_runs(
    owner_id: Annotated[str, Depends(current_user)],
    queue: Annotated[ProjectRunQueue, Depends(get_run_queue)],
):
    jobs = await asyncio.to_thread(queue.list, owner_id)
    return {"runs": [_public_job(job) for job in jobs]}


@router.get("/{job_id}")
async def get_run(
    job_id: uuid.UUID,
    owner_id: Annotated[str, Depends(current_user)],
    queue: Annotated[ProjectRunQueue, Depends(get_run_queue)],
):
    job = await asyncio.to_thread(queue.get, owner_id, job_id)
    if job is None:
        raise HTTPException(404, "Project run not found")
    return _public_job(job)


@router.post("/{job_id}/cancel")
async def cancel_run(
    job_id: uuid.UUID,
    owner_id: Annotated[str, Depends(current_user)],
    queue: Annotated[ProjectRunQueue, Depends(get_run_queue)],
):
    job = await asyncio.to_thread(queue.cancel, owner_id, job_id)
    if job is None:
        raise HTTPException(404, "Project run not found")
    return _public_job(job)
