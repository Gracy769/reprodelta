"""Health check router."""

from __future__ import annotations

import asyncio

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config import get_settings

router = APIRouter()


@router.get("/health", tags=["ops"])
async def health() -> dict:
    """Return service liveness status and version."""
    settings = get_settings()
    return {"status": "ok", "version": settings.app_version}


@router.get("/ready", tags=["ops"])
async def ready():
    """Probe dependencies, without claiming live model/authentication success."""
    from app.api.investigation_access import cookie_key
    from app.api.investigations import get_store

    settings = get_settings()
    checks = {}
    try:
        cookie_key()
        checks["session_key"] = "ok"
    except Exception:  # noqa: BLE001
        checks["session_key"] = "unavailable"
    try:
        await asyncio.to_thread(get_store().check_ready)
        checks["storage"] = "ok"
    except Exception:  # noqa: BLE001
        checks["storage"] = "unavailable"
    if settings.e2b_project_runs_enabled or settings.e2b_profile_validation_enabled:
        from app.storage.project_run_queue import ProjectRunQueue

        try:
            queue = ProjectRunQueue(settings.database_url)
            for kind, enabled in (
                ("investigation", settings.e2b_project_runs_enabled),
                ("profile_validation", settings.e2b_profile_validation_enabled),
            ):
                if enabled:
                    checks[f"project_worker_{kind}"] = (
                        "ok"
                        if await asyncio.to_thread(queue.has_live_worker, kind)
                        else "unavailable"
                    )
        except Exception:  # noqa: BLE001
            checks["project_worker"] = "unavailable"
    if settings.runner_mode == "worker_http":
        from pathlib import Path

        from runner.patching import project_digest, project_sources

        digest = project_digest(
            project_sources(Path(__file__).resolve().parents[2] / "fixture/project")
        )
        async with httpx.AsyncClient(timeout=5) as client:
            for kind, url, expected in (
                ("old", settings.worker_old_url, "1.26.4"),
                ("new", settings.worker_new_url, "2.0.2"),
            ):
                try:
                    response = await client.get(url.removesuffix("/run") + "/health")
                    data = response.json()
                    checks[f"worker_{kind}"] = (
                        "ok"
                        if response.is_success
                        and data.get("kind") == kind
                        and data.get("numpy_version") == expected
                        and data.get("project_sha256") == digest
                        else "mismatch"
                    )
                except (httpx.HTTPError, ValueError):
                    checks[f"worker_{kind}"] = "unavailable"
    else:
        checks["runner"] = "local_simulation"
    okay = all(value in ("ok", "local_simulation") for value in checks.values())
    return JSONResponse(
        {
            "status": "ready" if okay else "unavailable",
            "checks": checks,
            "live_nebius": "not_probed",
            "external_auth": "not_probed",
        },
        status_code=200 if okay else 503,
    )
