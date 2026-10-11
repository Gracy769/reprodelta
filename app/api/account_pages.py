"""Account entry and connected-source workbench pages."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.api.pages import templates
from app.config import get_settings

router = APIRouter()


def _context() -> dict[str, str | None]:
    return {"auth_base_url": get_settings().neon_auth_base_url}


@router.get("/signup", response_class=HTMLResponse, include_in_schema=False)
async def signup(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "auth.html", {**_context(), "mode": "signup"})


@router.get("/signin", response_class=HTMLResponse, include_in_schema=False)
async def signin(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "auth.html", {**_context(), "mode": "signin"})


@router.get("/workspace", response_class=HTMLResponse, include_in_schema=False)
async def workspace(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "workspace.html", _context())
