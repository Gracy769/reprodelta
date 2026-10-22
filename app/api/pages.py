"""HTML page routes."""

from __future__ import annotations

import asyncio
import hashlib
import pathlib
from functools import lru_cache

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.storage.project_shares import ProjectShareStore

TEMPLATES_DIR = pathlib.Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@lru_cache(maxsize=32)
def asset_url(filename: str) -> str:
    """Change the browser URL whenever a shipped asset's contents change."""
    digest = hashlib.sha256((TEMPLATES_DIR.parent / "static" / filename).read_bytes()).hexdigest()[:12]
    return f"/assets/{filename}?v={digest}"


templates.env.globals["asset_url"] = asset_url

router = APIRouter()

PUBLIC_ORIGIN = "https://reprodelta-web-production.up.railway.app"


@router.get("/robots.txt", include_in_schema=False)
async def robots() -> Response:
    body = (
        "User-agent: *\n"
        "Disallow: /api/\nDisallow: /auth/\nDisallow: /oauth/\n"
        "Disallow: /investigations/\nDisallow: /workspace\n"
        "Disallow: /signin\nDisallow: /signup\nDisallow: /shared/\n"
        f"Sitemap: {PUBLIC_ORIGIN}/sitemap.xml\n"
    )
    return Response(body, media_type="text/plain", headers={"Cache-Control": "public, max-age=3600"})


@router.get("/sitemap.xml", include_in_schema=False)
async def sitemap() -> Response:
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"<url><loc>{PUBLIC_ORIGIN}/</loc></url>"
        "</urlset>"
    )
    return Response(body, media_type="application/xml", headers={"Cache-Control": "public, max-age=3600"})


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


@router.get("/shared/{token}", response_class=HTMLResponse, include_in_schema=False)
async def shared_project_finding(request: Request, token: str) -> HTMLResponse:
    copy = await asyncio.to_thread(ProjectShareStore(get_settings().database_url).public, token)
    if copy is None:
        raise HTTPException(404, "Shared finding not found")
    return templates.TemplateResponse(
        request, "shared_project_run.html", {"finding": copy},
        headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"},
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
