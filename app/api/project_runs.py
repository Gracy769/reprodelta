"""Owner-scoped admission and status for approved-project investigations."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from psycopg import Error as PostgresError
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
from app.domain.project_hypothesis import model_summary
from app.domain.project_profile import ProfileApproval, ProjectProfileDraft
from app.domain.project_run import ProjectRunSpec
from app.domain.project_share import redacted_project_share
from app.orchestrator.project_replay_export import build_project_replay_bundle
from app.providers.nebius import NebiusClient
from app.storage.project_hypotheses import ProjectHypothesisStore
from app.storage.project_profiles import ProjectProfileStore
from app.storage.project_run_queue import ProjectRunQueue
from app.storage.project_shares import ProjectShareStore

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
    candidate_strategy: Literal["sweep", "seeded_random", "adaptive"] = "sweep"
    candidate_budget: int = Field(default=1, ge=1, le=32)
    candidate_seed: int = Field(default=0, ge=0, le=2_147_483_647)


def get_run_queue() -> ProjectRunQueue:
    return ProjectRunQueue(get_settings().database_url)


def get_profile_store() -> ProjectProfileStore:
    return ProjectProfileStore(get_settings().database_url)


def get_share_store() -> ProjectShareStore:
    return ProjectShareStore(get_settings().database_url)


def get_hypothesis_store() -> ProjectHypothesisStore:
    return ProjectHypothesisStore(get_settings().database_url)


class ShareConsent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acknowledge_public_share: bool


class ModelTransferConsent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acknowledge_numeric_summary_transfer: bool


def _request_project_hypothesis(summary: dict, settings):
    client = NebiusClient(settings.nebius_api_key, settings.nebius_model_id)
    return asyncio.run(client.propose_project_hypothesis(
        json.dumps(summary, sort_keys=True, separators=(",", ":"), allow_nan=False)
    ))


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
    candidate_strategy: Literal["sweep", "seeded_random", "adaptive"] = "sweep",
    candidate_budget: int = 1,
    candidate_seed: int = 0,
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
        or draft.sandbox_provider != get_settings().project_sandbox_provider
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
    rate = settings.project_sandbox_rate
    ceiling = settings.project_cost_ceiling
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
            "The selected source snapshot runs in an offline container on the operator's Oracle VM. "
            "Docker containers share the VM kernel; only reviewed projects are admitted."
            if settings.project_sandbox_provider == "oracle_docker" else
            "The selected source snapshot is sent to E2B for isolated execution. "
            "Review the provider's retention and deletion policy before approving."
        ),
        "max_wall_seconds": 900,
        "max_sandbox_seconds": max_sandbox_seconds,
        "estimated_max_sandbox_cost_usd": round(max_sandbox_seconds * rate, 6)
        if rate_ready else None,
        "cost_ceiling_usd": ceiling if rate_ready else None,
        "rate_configured": rate_ready,
        "execution_enabled": settings.project_runs_enabled,
        "candidate_strategy": candidate_strategy,
        "candidate_budget": candidate_budget,
        "candidate_seed": candidate_seed,
    }
    if not eligibility["eligible"] or not snapshot or not rate_ready:
        return plan, None
    _, source_digest = await immutable_snapshot(
        _host_get, provider, token, source_id, review["base_sha"]
    )
    if source_digest != approval.validated_source_sha256:
        raise HTTPException(409, "This source snapshot was not validated for the approved profile")
    if await change_refs(_host_get, provider, token, source_id, change_number) != (
        review["base_sha"], review["head_sha"]
    ):
        raise HTTPException(409, "PR/MR changed during admission; review the latest revision")
    plan["source_sha256"] = source_digest
    spec = ProjectRunSpec(
        profile_id=profile_id,
        sandbox_provider=settings.project_sandbox_provider,
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
        candidate_strategy=candidate_strategy,
        candidate_budget=candidate_budget,
        candidate_seed=candidate_seed,
    )
    return plan, spec


@router.get("/profiles/{profile_id}/changes/{change_number}/plan")
async def run_plan(
    profile_id: uuid.UUID,
    change_number: int,
    owner_id: Annotated[str, Depends(current_user)],
    profiles: Annotated[ProjectProfileStore, Depends(get_profile_store)],
    queue: Annotated[ProjectRunQueue, Depends(get_run_queue)],
    candidate_strategy: Literal["sweep", "seeded_random", "adaptive"] = "sweep",
    candidate_budget: int = 1,
    candidate_seed: int = 0,
):
    if not 1 <= change_number <= 10_000_000:
        raise HTTPException(422, "Invalid change number")
    if not 1 <= candidate_budget <= 32 or not 0 <= candidate_seed <= 2_147_483_647:
        raise HTTPException(422, "Invalid candidate budget or seed")
    plan, _ = await _plan(
        owner_id, profile_id, change_number, profiles, snapshot=False,
        candidate_strategy=candidate_strategy, candidate_budget=candidate_budget,
        candidate_seed=candidate_seed,
    )
    plan["feature_enabled"] = plan["execution_enabled"]
    if plan["execution_enabled"]:
        try:
            plan["worker_available"] = await asyncio.to_thread(
                queue.has_live_worker,
                ProjectRunQueue.capability("investigation", get_settings().project_sandbox_provider)
            )
        except PostgresError:
            plan["worker_available"] = False
        plan["execution_enabled"] = plan["worker_available"]
    else:
        plan["worker_available"] = False
    return plan


@router.post("", status_code=202)
async def start_run(
    body: StartProjectRun,
    owner_id: Annotated[str, Depends(current_user)],
    profiles: Annotated[ProjectProfileStore, Depends(get_profile_store)],
    queue: Annotated[ProjectRunQueue, Depends(get_run_queue)],
):
    if not get_settings().project_runs_enabled:
        raise HTTPException(503, "Approved-project execution is not enabled")
    try:
        worker_available = await asyncio.to_thread(
            queue.has_live_worker,
            ProjectRunQueue.capability("investigation", get_settings().project_sandbox_provider),
        )
    except PostgresError as exc:
        raise HTTPException(503, "Investigation worker status is unavailable") from exc
    if not worker_available:
        raise HTTPException(503, "Investigation worker is unavailable; try again later")
    if not body.source_transfer_approved:
        raise HTTPException(422, "Approve the displayed execution location before starting")
    plan, spec = await _plan(
        owner_id, body.profile_id, body.change_number, profiles, snapshot=True,
        candidate_strategy=body.candidate_strategy, candidate_budget=body.candidate_budget,
        candidate_seed=body.candidate_seed,
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


@router.get("/shares/{token}")
async def public_share(
    token: str,
    response: Response,
    shares: Annotated[ProjectShareStore, Depends(get_share_store)],
):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex"
    copy = await asyncio.to_thread(shares.public, token)
    if copy is None:
        raise HTTPException(404, "Shared finding not found")
    return copy


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


@router.get("/{job_id}/model-summary")
async def project_model_summary(
    job_id: uuid.UUID,
    owner_id: Annotated[str, Depends(current_user)],
    queue: Annotated[ProjectRunQueue, Depends(get_run_queue)],
):
    job = await asyncio.to_thread(queue.get, owner_id, job_id)
    if job is None:
        raise HTTPException(404, "Project run not found")
    try:
        summary = model_summary(job)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    settings = get_settings()
    return {
        "summary": summary,
        "model_id": settings.nebius_model_id,
        "enabled": bool(settings.project_hypotheses_enabled and settings.nebius_api_key
                        and settings.nebius_call_cost_ceiling_usd > 0),
        "configured_per_call_ceiling_usd": settings.nebius_call_cost_ceiling_usd,
        "notice": "Only this numeric summary is sent to Nebius after your approval. "
                  "The response is an untested suggestion, not an executed finding.",
    }


@router.get("/{job_id}/model-hypothesis")
async def get_project_hypothesis(
    job_id: uuid.UUID,
    owner_id: Annotated[str, Depends(current_user)],
    queue: Annotated[ProjectRunQueue, Depends(get_run_queue)],
    hypotheses: Annotated[ProjectHypothesisStore, Depends(get_hypothesis_store)],
):
    if await asyncio.to_thread(queue.get, owner_id, job_id) is None:
        raise HTTPException(404, "Project run not found")
    saved = await asyncio.to_thread(hypotheses.get, owner_id, job_id)
    if saved is None:
        raise HTTPException(404, "No model suggestion has been requested")
    return saved


@router.post("/{job_id}/model-hypothesis", status_code=202)
async def request_project_hypothesis(
    job_id: uuid.UUID,
    body: ModelTransferConsent,
    owner_id: Annotated[str, Depends(current_user)],
    queue: Annotated[ProjectRunQueue, Depends(get_run_queue)],
    hypotheses: Annotated[ProjectHypothesisStore, Depends(get_hypothesis_store)],
):
    if not body.acknowledge_numeric_summary_transfer:
        raise HTTPException(422, "Approve the displayed numeric summary transfer")
    job = await asyncio.to_thread(queue.get, owner_id, job_id)
    if job is None:
        raise HTTPException(404, "Project run not found")
    try:
        summary = model_summary(job)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    settings = get_settings()
    if (not settings.project_hypotheses_enabled or not settings.nebius_api_key
            or not 0 < settings.nebius_call_cost_ceiling_usd <= 100
            or not math.isfinite(settings.nebius_call_cost_ceiling_usd)):
        raise HTTPException(503, "Model suggestions are not enabled with a verified cost ceiling")
    reserved, created = await asyncio.to_thread(
        hypotheses.reserve, owner_id, job_id, summary=summary,
        model_id=settings.nebius_model_id,
        ceiling_usd=settings.nebius_call_cost_ceiling_usd,
    )
    if not created:
        return reserved
    try:
        proposal = await asyncio.to_thread(_request_project_hypothesis, summary, settings)
        if proposal.is_mock or proposal.model_id != settings.nebius_model_id:
            raise ValueError("The configured live model did not answer")
        usage = {key: value for key, value in (proposal.usage or {}).items()
                 if key in {"prompt_tokens", "completion_tokens", "total_tokens"}
                 and type(value) is int and 0 <= value <= 10_000_000}
        saved = await asyncio.to_thread(
            hypotheses.complete, owner_id, job_id,
            request_digest=proposal.request_digest,
            response_text=proposal.response_text,
            usage=usage,
        )
        if saved is None:
            raise ValueError("The proposal reservation was lost")
        return saved
    except Exception as exc:
        # Provider errors can include private request contents. Expose only a
        # stable status, and never send a second billable request for this run.
        await asyncio.to_thread(hypotheses.fail, owner_id, job_id)
        raise HTTPException(503, "Model suggestion failed; no scientific finding changed") from exc


@router.get("/{job_id}/replay.zip")
async def download_private_replay(
    job_id: uuid.UUID,
    owner_id: Annotated[str, Depends(current_user)],
    queue: Annotated[ProjectRunQueue, Depends(get_run_queue)],
    profiles: Annotated[ProjectProfileStore, Depends(get_profile_store)],
):
    """Download exact private source and recorded witness for local replay."""
    job = await asyncio.to_thread(queue.get, owner_id, job_id)
    if job is None:
        raise HTTPException(404, "Project run not found")
    if job["status"] != "completed":
        raise HTTPException(409, "Complete the project run before exporting")
    spec = ProjectRunSpec.model_validate(job["spec_json"])
    profile = await asyncio.to_thread(profiles.get, owner_id, spec.profile_id)
    if profile is None:
        raise HTTPException(409, "Approved project profile is unavailable")
    source_id = (_github_repository(spec.repository) if spec.provider == "github"
                 else _gitlab_project(spec.repository))
    token = await asyncio.to_thread(_saved_token, owner_id, spec.provider)
    files, digest = await immutable_snapshot(
        _host_get, spec.provider, token, source_id, spec.base_sha
    )
    if digest != spec.source_sha256:
        raise HTTPException(409, "The immutable source snapshot no longer matches")
    try:
        bundle = await asyncio.to_thread(build_project_replay_bundle, job, profile, files)
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(409, "Saved evidence cannot be exported as a replay bundle") from exc
    return Response(
        content=bundle, media_type="application/zip",
        headers={
            "Cache-Control": "no-store",
            "X-Robots-Tag": "noindex",
            "X-ReproDelta-Zip-SHA256": hashlib.sha256(bundle).hexdigest(),
            "Content-Disposition": f'attachment; filename="reprodelta-{job_id}-replay.zip"',
        },
    )


@router.get("/{job_id}/share-preview")
async def share_preview(
    job_id: uuid.UUID,
    owner_id: Annotated[str, Depends(current_user)],
    queue: Annotated[ProjectRunQueue, Depends(get_run_queue)],
):
    job = await asyncio.to_thread(queue.get, owner_id, job_id)
    if job is None:
        raise HTTPException(404, "Project run not found")
    try:
        return redacted_project_share(job)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/{job_id}/shares", status_code=201)
async def create_share(
    job_id: uuid.UUID,
    body: ShareConsent,
    owner_id: Annotated[str, Depends(current_user)],
    queue: Annotated[ProjectRunQueue, Depends(get_run_queue)],
    shares: Annotated[ProjectShareStore, Depends(get_share_store)],
):
    if not body.acknowledge_public_share:
        raise HTTPException(422, "Confirm that the redacted copy may be viewed by anyone with its link")
    job = await asyncio.to_thread(queue.get, owner_id, job_id)
    if job is None:
        raise HTTPException(404, "Project run not found")
    try:
        copy = redacted_project_share(job)
        saved = await asyncio.to_thread(shares.create, owner_id, job_id, copy)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"id": saved["id"], "url": f"/shared/{saved['token']}",
            "created_at": saved["created_at"]}


@router.get("/{job_id}/shares")
async def list_shares(
    job_id: uuid.UUID,
    owner_id: Annotated[str, Depends(current_user)],
    queue: Annotated[ProjectRunQueue, Depends(get_run_queue)],
    shares: Annotated[ProjectShareStore, Depends(get_share_store)],
):
    if await asyncio.to_thread(queue.get, owner_id, job_id) is None:
        raise HTTPException(404, "Project run not found")
    return {"shares": await asyncio.to_thread(shares.list, owner_id, job_id)}


@router.delete("/{job_id}/shares/{share_id}", status_code=204)
async def revoke_share(
    job_id: uuid.UUID,
    share_id: uuid.UUID,
    owner_id: Annotated[str, Depends(current_user)],
    shares: Annotated[ProjectShareStore, Depends(get_share_store)],
):
    if not await asyncio.to_thread(shares.revoke, owner_id, job_id, share_id):
        raise HTTPException(404, "Shared finding not found")


@router.get("/{job_id}/revision")
async def get_run_revision(
    job_id: uuid.UUID,
    owner_id: Annotated[str, Depends(current_user)],
    queue: Annotated[ProjectRunQueue, Depends(get_run_queue)],
):
    """Check a saved run against the source host without changing its evidence."""
    job = await asyncio.to_thread(queue.get, owner_id, job_id)
    if job is None:
        raise HTTPException(404, "Project run not found")
    spec = ProjectRunSpec.model_validate(job["spec_json"])
    source_id = (
        _github_repository(spec.repository)
        if spec.provider == "github"
        else _gitlab_project(spec.repository)
    )
    token = await asyncio.to_thread(_saved_token, owner_id, spec.provider)
    base_sha, head_sha = await change_refs(
        _host_get, spec.provider, token, source_id, spec.change_number
    )
    return {
        "saved_base_sha": spec.base_sha,
        "saved_head_sha": spec.head_sha,
        "current_base_sha": base_sha,
        "current_head_sha": head_sha,
        "changed": (base_sha, head_sha) != (spec.base_sha, spec.head_sha),
    }


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
