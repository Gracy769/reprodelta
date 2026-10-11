"""HTML page routes."""

from __future__ import annotations

import pathlib

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.domain.contract import FIXTURE_CONTRACT, hash_contract

TEMPLATES_DIR = pathlib.Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

router = APIRouter()

DEMO_PROJECT_ID = "00000000-0000-0000-0000-000000000001"


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def index(request: Request) -> HTMLResponse:
    """Render the ReproDelta workbench home page."""
    return templates.TemplateResponse(
        request,
        "index.html",
    )


@router.get("/investigations/new", response_class=HTMLResponse, include_in_schema=False)
async def investigation_new(request: Request) -> HTMLResponse:
    """Render the new-investigation setup page."""
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
