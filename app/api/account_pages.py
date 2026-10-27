"""Account entry and connected-source workbench pages."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.api.pages import templates
from app.config import get_settings

router = APIRouter()


def _context(request: Request) -> dict[str, object]:
    settings = get_settings()
    firebase_config = None
    edge_host = request.headers.get("x-reprodelta-edge-host")
    edge_url = getattr(settings, "edge_url", None)
    use_edge = bool(edge_url and edge_host == edge_url.removeprefix("https://").rstrip("/"))
    prefix = "firebase_edge" if use_edge else "firebase"
    values = [getattr(settings, f"{prefix}_{field}", None)
              for field in ("api_key", "auth_domain", "project_id", "app_id")]
    if all(values):
        firebase_config = dict(zip(("apiKey", "authDomain", "projectId", "appId"), values, strict=True))
    return {
        "auth_base_url": settings.neon_auth_base_url,
        "firebase_config": firebase_config,
        "gitlab_oauth_enabled": bool(settings.gitlab_oauth_client_id and settings.gitlab_oauth_client_secret),
        "sandbox_provider": getattr(settings, "project_sandbox_provider", "e2b"),
    }


@router.get("/signup", response_class=HTMLResponse, include_in_schema=False)
async def signup(request: Request) -> HTMLResponse:
    if _signed_in(request):
        return RedirectResponse("/workspace", status_code=303)
    return templates.TemplateResponse(request, "auth.html", {**_context(request), "mode": "signup"})


@router.get("/signin", response_class=HTMLResponse, include_in_schema=False)
async def signin(request: Request) -> HTMLResponse:
    if _signed_in(request):
        return RedirectResponse("/workspace", status_code=303)
    return templates.TemplateResponse(request, "auth.html", {**_context(request), "mode": "signin"})


def _signed_in(request: Request) -> bool:
    from fastapi import HTTPException

    from app.api.source_oauth import _session_claims

    try:
        _session_claims(request.cookies.get("rd_session"))
        return True
    except HTTPException:
        return False


@router.get("/workspace", response_class=HTMLResponse, include_in_schema=False)
async def workspace(request: Request) -> Response:
    verifier = request.query_params.get("neon_auth_session_verifier")
    if verifier:
        from app.api.auth_bridge import exchange_oauth_verifier

        return await exchange_oauth_verifier(request, verifier)
    return templates.TemplateResponse(request, "workspace.html", _context(request))
