"""Guided setup records for operator-approved scientific Python projects."""

from __future__ import annotations

import asyncio
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

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
from app.domain.project_eligibility import check_eligibility
from app.domain.project_profile import ProfileApproval, ProjectProfileDraft
from app.storage.project_profiles import ProjectProfileStore

router = APIRouter(prefix="/api/project-profiles", tags=["project profiles"])


def get_profile_store() -> ProjectProfileStore:
    return ProjectProfileStore(get_settings().database_url)


@router.post("", status_code=201)
async def create_profile(
    body: ProjectProfileDraft,
    owner_id: Annotated[str, Depends(current_user)],
    store: Annotated[ProjectProfileStore, Depends(get_profile_store)],
):
    return await asyncio.to_thread(store.create, owner_id, body)


@router.get("")
async def list_profiles(
    owner_id: Annotated[str, Depends(current_user)],
    store: Annotated[ProjectProfileStore, Depends(get_profile_store)],
):
    return {"profiles": await asyncio.to_thread(store.list, owner_id)}


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
    return {
        **result,
        "pilot_enabled": get_settings().e2b_project_runs_enabled,
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
    allowed = {
        value.strip()
        for value in get_settings().profile_approver_subjects.split(",")
        if value.strip()
    }
    if approver not in allowed:
        raise HTTPException(403, "Only a configured project approver may approve execution")
    profile = await asyncio.to_thread(store.get_any, profile_id)
    if profile is None:
        raise HTTPException(404, "Profile not found")
    if profile["draft_json"].get("source_transfer_consent") is not True:
        raise HTTPException(
            409, "Owner must consent to source transfer to E2B in a new profile version"
        )
    try:
        result = await asyncio.to_thread(
            store.approve, profile["owner_id"], profile_id, approver, body
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return result
