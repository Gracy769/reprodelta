"""HTML page routes."""

from __future__ import annotations

import pathlib

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.domain.contract import FIXTURE_CONTRACT, hash_contract

TEMPLATES_DIR = pathlib.Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter()

DEMO_PROJECT_ID = "00000000-0000-0000-0000-000000000001"


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index(request: Request) -> Response:
    """Render the ReproDelta workbench home page."""
    verifier = request.query_params.get("neon_auth_session_verifier")
    if verifier:
        from app.api.auth_bridge import exchange_oauth_verifier

        return await exchange_oauth_verifier(request, verifier)
    return templates.TemplateResponse(
        request,
        "index.html",
    )


@router.get("/investigations/new", response_class=HTMLResponse, include_in_schema=False)
async def investigation_new(request: Request) -> HTMLResponse:
    """Choose a connected source and change for a new investigation."""
    settings = get_settings()
    return templates.TemplateResponse(request, "workspace.html", {
        "auth_base_url": settings.neon_auth_base_url,
        "gitlab_oauth_enabled": bool(settings.gitlab_oauth_client_id and settings.gitlab_oauth_client_secret),
        "new_investigation": True,
    })


@router.get("/demo/investigation", response_class=HTMLResponse, include_in_schema=False)
async def demo_investigation(request: Request) -> HTMLResponse:
    """Render the isolated, curated NumPy demonstration."""
    return templates.TemplateResponse(
        request,
        "investigation_new.html",
        {
            "project_id": DEMO_PROJECT_ID,
            "contract": FIXTURE_CONTRACT,
            "contract_hash": hash_contract(FIXTURE_CONTRACT),
        },
    )


@router.get("/investigations/{investigation_id}", response_class=HTMLResponse, include_in_schema=False)
async def investigation_running(request: Request, investigation_id: str) -> HTMLResponse:
    """Render the live investigation progress page."""
    return templates.TemplateResponse(
        request,
        "investigation_running.html",
        {"investigation_id": investigation_id},
    )


@router.get("/investigations/{investigation_id}/evidence", response_class=HTMLResponse, include_in_schema=False)
async def investigation_evidence(request: Request, investigation_id: str) -> HTMLResponse:
    """Render the investigation evidence page."""
    # The template fetches evidence data via the API endpoint at load time.
    return templates.TemplateResponse(
        request,
        "evidence.html",
        {"investigation_id": investigation_id},
    )


@router.get("/investigations/{investigation_id}/report", response_class=HTMLResponse, include_in_schema=False)
async def investigation_report(request: Request, investigation_id: str) -> HTMLResponse:
    """Render the printable/shareable report page."""
    return templates.TemplateResponse(
        request,
        "report.html",
        {"investigation_id": investigation_id},
    )
