"""Guided setup records for operator-approved scientific Python projects."""

from __future__ import annotations

import asyncio
import math
import uuid
from contextlib import suppress
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
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
from app.api.source_analysis import analyze
from app.config import get_settings
from app.domain.profile_validation import ProfileValidationSpec
from app.domain.project_eligibility import check_eligibility
from app.domain.project_profile import PreparedEnvironment, ProfileApproval, ProjectProfileDraft
from app.storage.pilot_metrics import PilotMetricsStore
from app.storage.project_profiles import ProjectProfileStore
from app.storage.project_run_queue import ProjectRunQueue

router = APIRouter(prefix="/api/project-profiles", tags=["project profiles"])


def get_profile_store() -> ProjectProfileStore:
    return ProjectProfileStore(get_settings().database_url)


def get_validation_queue() -> ProjectRunQueue:
    return ProjectRunQueue(get_settings().database_url)


def get_pilot_metrics() -> PilotMetricsStore:
    return PilotMetricsStore(get_settings().database_url)


class ValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_sha: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    old: PreparedEnvironment
    new: PreparedEnvironment
    idempotency_key: str = Field(min_length=8, max_length=128)


def _approvers() -> set[str]:
    return {
        value.strip()
        for value in get_settings().profile_approver_subjects.split(",")
        if value.strip()
    }


def _public_validation(job: dict) -> dict:
    return {
        key: value for key, value in job.items()
        if key not in {"owner_id", "lease_token", "lease_expires_at", "idempotency_key"}
    }


@router.post("", status_code=201)
async def create_profile(
    body: ProjectProfileDraft,
    owner_id: Annotated[str, Depends(current_user)],
    store: Annotated[ProjectProfileStore, Depends(get_profile_store)],
):
    if body.sandbox_provider != get_settings().project_sandbox_provider:
        raise HTTPException(422, "Profile execution location differs from this deployment")
    return await asyncio.to_thread(store.create, owner_id, body)


@router.get("")
async def list_profiles(
    owner_id: Annotated[str, Depends(current_user)],
    store: Annotated[ProjectProfileStore, Depends(get_profile_store)],
):
    return {"profiles": await asyncio.to_thread(store.list, owner_id)}


@router.get("/operator/pending")
async def pending_profiles(
    approver: Annotated[str, Depends(current_user)],
    store: Annotated[ProjectProfileStore, Depends(get_profile_store)],
):
    if approver not in _approvers():
        raise HTTPException(403, "Only a configured project approver may review drafts")
    return {"profiles": await asyncio.to_thread(store.list_pending)}


@router.get("/operator/pilot-metrics")
async def pilot_metrics(
    approver: Annotated[str, Depends(current_user)],
    metrics: Annotated[PilotMetricsStore, Depends(get_pilot_metrics)],
):
    if approver not in _approvers():
        raise HTTPException(403, "Only a configured project approver may inspect pilot metrics")
    return await asyncio.to_thread(metrics.read)


@router.get("/{profile_id}")
async def get_profile(
    profile_id: uuid.UUID,
    owner_id: Annotated[str, Depends(current_user)],
    store: Annotated[ProjectProfileStore, Depends(get_profile_store)],
):
    result = await asyncio.to_thread(store.get, owner_id, profile_id)
    if result is None:
        raise HTTPException(404, "Profile not found")
    return result


@router.get("/{profile_id}/changes/{number}/eligibility")
async def change_eligibility(
    profile_id: uuid.UUID,
    number: int,
    owner_id: Annotated[str, Depends(current_user)],
    store: Annotated[ProjectProfileStore, Depends(get_profile_store)],
    metrics: Annotated[PilotMetricsStore, Depends(get_pilot_metrics)],
):
    if number < 1 or number > 10_000_000:
        raise HTTPException(422, "Invalid change number")
    profile = await asyncio.to_thread(store.get, owner_id, profile_id)
    if profile is None:
        raise HTTPException(404, "Profile not found")
    provider = profile["provider"]
    repository = profile["repository"]
    source_id = (
        _github_repository(repository) if provider == "github" else _gitlab_project(repository)
    )
    token = await asyncio.to_thread(_saved_token, owner_id, provider)
    review = await analyze(_host_get, _host_pages, provider, token, source_id, repository, number)
    result = check_eligibility(profile, review)
    # The owner can still inspect eligibility if optional pilot telemetry is down.
    with suppress(PostgresError):
        await asyncio.to_thread(metrics.record_eligibility, profile_id, number, review, result)
    return {
        **result,
        "pilot_enabled": get_settings().project_runs_enabled,
        "changed_file_count": review["changed_file_count_checked"],
        "dependency_changes": review["dependency_changes"],
    }


@router.post("/{profile_id}/approve")
async def approve_profile(
    profile_id: uuid.UUID,
    body: ProfileApproval,
    approver: Annotated[str, Depends(current_user)],
    store: Annotated[ProjectProfileStore, Depends(get_profile_store)],
):
    if approver not in _approvers():
        raise HTTPException(403, "Only a configured project approver may approve execution")
    profile = await asyncio.to_thread(store.get_any, profile_id)
    if profile is None:
        raise HTTPException(404, "Profile not found")
    if profile["draft_json"].get("source_transfer_consent") is not True:
        raise HTTPException(
            409, "Owner must consent to source transfer in a new profile version"
        )
    if profile["draft_json"].get("sandbox_provider", "e2b") != get_settings().project_sandbox_provider:
        raise HTTPException(409, "Create a new profile with consent for this execution location")
    try:
        result = await asyncio.to_thread(
            store.approve, profile["owner_id"], profile_id, approver, body
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return result


@router.post("/{profile_id}/validations", status_code=202)
async def request_validation(
    profile_id: uuid.UUID,
    body: ValidationRequest,
    approver: Annotated[str, Depends(current_user)],
    profiles: Annotated[ProjectProfileStore, Depends(get_profile_store)],
    queue: Annotated[ProjectRunQueue, Depends(get_validation_queue)],
):
    settings = get_settings()
    if approver not in _approvers():
        raise HTTPException(403, "Only a configured project approver may validate execution")
    if not settings.profile_validation_enabled:
        raise HTTPException(503, "Approved-project sample validation is not enabled")
    try:
        worker_available = await asyncio.to_thread(
            queue.has_live_worker,
            ProjectRunQueue.capability("profile_validation", settings.project_sandbox_provider),
        )
    except PostgresError as exc:
        raise HTTPException(503, "Validation worker status is unavailable") from exc
    if not worker_available:
        raise HTTPException(503, "Validation worker is unavailable; try again later")
    profile = await asyncio.to_thread(profiles.get_any, profile_id)
    if profile is None:
        raise HTTPException(404, "Profile not found")
    if profile["status"] != "draft":
        raise HTTPException(409, "Only a draft profile can be validated")
    draft = ProjectProfileDraft.model_validate(profile["draft_json"])
    if (not draft.source_transfer_consent or draft.digest != profile["profile_digest"]
            or draft.sandbox_provider != settings.project_sandbox_provider):
        raise HTTPException(409, "Owner consent or immutable profile digest is missing")
    rate = settings.project_sandbox_rate
    ceiling = settings.project_cost_ceiling
    if not math.isfinite(rate) or not math.isfinite(ceiling) or rate <= 0 or ceiling <= 0:
        raise HTTPException(503, "A verified sandbox rate and cost ceiling are required")
    allowed_seconds = min(600, math.floor(ceiling / rate))
    if allowed_seconds < 120:
        raise HTTPException(503, "Cost ceiling is too small for paired sample validation")
    try:
        spec = ProfileValidationSpec(
            profile_id=profile_id, sandbox_provider=settings.project_sandbox_provider,
            provider=draft.provider, repository=draft.repository,
            source_sha=body.source_sha, profile_digest=draft.digest,
            contract_hash=draft.contract_hash, old=body.old, new=body.new,
            max_sandbox_seconds=allowed_seconds,
            sandbox_usd_per_second=rate, max_cost_usd=ceiling,
        )
        job = await asyncio.to_thread(
            queue.submit_validation, profile["owner_id"], body.idempotency_key, spec
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _public_validation(job)


@router.get("/{profile_id}/validations")
async def list_validations(
    profile_id: uuid.UUID,
    user_id: Annotated[str, Depends(current_user)],
    profiles: Annotated[ProjectProfileStore, Depends(get_profile_store)],
    queue: Annotated[ProjectRunQueue, Depends(get_validation_queue)],
):
    profile = await asyncio.to_thread(profiles.get_any, profile_id)
    if profile is None or (user_id != profile["owner_id"] and user_id not in _approvers()):
        raise HTTPException(404, "Profile not found")
    jobs = await asyncio.to_thread(queue.list_validations, profile["owner_id"], profile_id)
    return {"validations": [_public_validation(job) for job in jobs]}


@router.get("/{profile_id}/validations/{job_id}")
async def validation_status(
    profile_id: uuid.UUID,
    job_id: uuid.UUID,
    user_id: Annotated[str, Depends(current_user)],
    profiles: Annotated[ProjectProfileStore, Depends(get_profile_store)],
    queue: Annotated[ProjectRunQueue, Depends(get_validation_queue)],
):
    profile = await asyncio.to_thread(profiles.get_any, profile_id)
    if profile is None or (user_id != profile["owner_id"] and user_id not in _approvers()):
        raise HTTPException(404, "Validation not found")
    job = await asyncio.to_thread(
        queue.get, profile["owner_id"], job_id, kind="profile_validation"
    )
    if job is None or job["spec_json"].get("profile_id") != str(profile_id):
        raise HTTPException(404, "Validation not found")
    return _public_validation(job)


@router.delete("/{profile_id}/validations/{job_id}")
async def cancel_validation(
    profile_id: uuid.UUID,
    job_id: uuid.UUID,
    user_id: Annotated[str, Depends(current_user)],
    profiles: Annotated[ProjectProfileStore, Depends(get_profile_store)],
    queue: Annotated[ProjectRunQueue, Depends(get_validation_queue)],
):
    profile = await asyncio.to_thread(profiles.get_any, profile_id)
    if profile is None or (user_id != profile["owner_id"] and user_id not in _approvers()):
        raise HTTPException(404, "Validation not found")
    job = await asyncio.to_thread(
        queue.get, profile["owner_id"], job_id, kind="profile_validation"
    )
    if job is None or job["spec_json"].get("profile_id") != str(profile_id):
        raise HTTPException(404, "Validation not found")
    result = await asyncio.to_thread(
        queue.cancel, profile["owner_id"], job_id, kind="profile_validation"
    )
    return _public_validation(result)
