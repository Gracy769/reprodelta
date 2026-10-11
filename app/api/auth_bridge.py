"""Narrow first-party bridge to managed Neon Auth session endpoints.

Browsers can reject third-party cookies for an Auth host on another domain.
The bridge keeps email/password and session requests on ReproDelta's origin.
Social OAuth still uses the provider's managed redirect flow.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from app.config import get_settings

router = APIRouter(prefix="/auth", include_in_schema=False)

ALLOWED = {
    ("POST", "sign-up/email"),
    ("POST", "sign-in/email"),
    ("POST", "sign-in/social"),
    ("POST", "sign-out"),
    ("GET", "get-session"),
    ("GET", "token"),
}


def _auth_cookies(request: Request) -> str:
    approved = []
    for item in request.headers.get("cookie", "").split(";"):
        name = item.strip().split("=", 1)[0]
        if name.startswith(("__Secure-neon-auth.", "__Host-neon-auth.", "neon-auth.", "better-auth.")):
            approved.append(item.strip())
    return "; ".join(approved)


def _copy_auth_cookies(upstream: httpx.Response, response: Response) -> None:
    for cookie in upstream.headers.get_list("set-cookie"):
        if "domain=" in cookie.lower():
            continue
        # Neon emits partitioned cross-site cookies. On our first-party bridge,
        # Lax lets the OAuth challenge survive the provider's top-level redirect.
        cookie = re.sub(r";\s*Partitioned(?=;|$)", "", cookie, flags=re.I)
        cookie = re.sub(r";\s*SameSite=[^;]+", "; SameSite=Lax", cookie, flags=re.I)
        response.raw_headers.append((b"set-cookie", cookie.encode("latin-1")))


async def exchange_oauth_verifier(request: Request, verifier: str) -> RedirectResponse:
    """Complete Neon's redirect flow on the same origin that set its challenge cookie."""
    settings = get_settings()
    cookies = _auth_cookies(request)
    challenge = ("session_challenge=", "session_challange=")
    if not settings.neon_auth_base_url or not any(part in cookies for part in challenge):
        return RedirectResponse("/signin?auth_error=oauth", status_code=303)
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=False) as client:
            upstream = await client.get(
                settings.neon_auth_base_url.rstrip("/") + "/get-session",
                params={"neon_auth_session_verifier": verifier},
                headers={"Accept": "application/json", "Cookie": cookies},
            )
    except httpx.HTTPError:
        return RedirectResponse("/signin?auth_error=oauth", status_code=303)
    try:
        signed_in = upstream.is_success and bool(upstream.json().get("user"))
    except (ValueError, AttributeError):
        signed_in = False
    if not signed_in:
        return RedirectResponse("/signin?auth_error=oauth", status_code=303)
    response = RedirectResponse("/workspace", status_code=303)
    _copy_auth_cookies(upstream, response)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.api_route("/{path:path}", methods=["GET", "POST"])
async def bridge_auth(request: Request, path: str) -> Response:
    if (request.method, path) not in ALLOWED:
        raise HTTPException(404, "Auth route not found")
    settings = get_settings()
    base = settings.neon_auth_base_url
    if not base or not base.startswith("https://"):
        raise HTTPException(503, "Sign-in is not configured")
    origin = request.headers.get("origin")
    if request.method == "POST":
        if not origin:
            raise HTTPException(403, "Origin is required")
        parts = urlsplit(origin)
        configured_origin = settings.auth_allowed_origin or settings.app_url
        public_host = urlsplit(configured_origin).netloc if configured_origin else ""
        host = public_host or request.headers.get("host", "")
        scheme_ok = parts.scheme == "https" or (
            parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1"}
        )
        if not scheme_ok or parts.netloc != host or parts.path not in {"", "/"}:
            raise HTTPException(403, "Origin is not allowed")
    body = await request.body()
    if len(body) > 16_384:
        raise HTTPException(413, "Auth request is too large")
    headers = {"Accept": "application/json"}
    if request.headers.get("content-type"):
        headers["Content-Type"] = request.headers["content-type"]
    if _auth_cookies(request):
        headers["Cookie"] = _auth_cookies(request)
    if origin:
        headers["Origin"] = origin
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=False) as client:
            upstream = await client.request(
                request.method,
                base.rstrip("/") + "/" + path,
                headers=headers,
                content=body,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(502, "Account service is unavailable") from exc
    response = Response(
        content=upstream.content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type", "application/json").split(";", 1)[0],
    )
    _copy_auth_cookies(upstream, response)
    response.headers["Cache-Control"] = "no-store"
    return response
