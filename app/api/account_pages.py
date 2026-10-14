"""Account entry and connected-source workbench pages."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.api.pages import templates
from app.config import get_settings

router = APIRouter()


def _context() -> dict[str, object]:
    settings = get_settings()
    firebase_config = None
    if all((settings.firebase_api_key, settings.firebase_auth_domain,
            settings.firebase_project_id, settings.firebase_app_id)):
        firebase_config = {"apiKey": settings.firebase_api_key, "authDomain": settings.firebase_auth_domain,
                           "projectId": settings.firebase_project_id, "appId": settings.firebase_app_id}
    return {
        "auth_base_url": settings.neon_auth_base_url,
        "firebase_config": firebase_config,
        "gitlab_oauth_enabled": bool(settings.gitlab_oauth_client_id and settings.gitlab_oauth_client_secret),
    }


@router.get("/signup", response_class=HTMLResponse, include_in_schema=False)
async def signup(request: Request) -> HTMLResponse:
    if _signed_in(request):
        return RedirectResponse("/workspace", status_code=303)
    return templates.TemplateResponse(request, "auth.html", {**_context(), "mode": "signup"})


@router.get("/signin", response_class=HTMLResponse, include_in_schema=False)
async def signin(request: Request) -> HTMLResponse:
    if _signed_in(request):
        return RedirectResponse("/workspace", status_code=303)
    return templates.TemplateResponse(request, "auth.html", {**_context(), "mode": "signin"})


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
    return templates.TemplateResponse(request, "workspace.html", _context())
