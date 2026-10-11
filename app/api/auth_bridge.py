"""Narrow first-party bridge to managed Neon Auth session endpoints.

Browsers can reject third-party cookies for an Auth host on another domain.
The bridge keeps email/password and session requests on ReproDelta's origin.
Social OAuth still uses the provider's managed redirect flow.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from app.config import get_settings

router = APIRouter(prefix="/auth", include_in_schema=False)

ALLOWED = {
    ("POST", "sign-up/email"),
    ("POST", "sign-in/email"),
    ("POST", "sign-out"),
    ("GET", "get-session"),
    ("GET", "token"),
}


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
    if request.headers.get("cookie"):
        approved = []
        for item in request.headers["cookie"].split(";"):
            name = item.strip().split("=", 1)[0]
            if name.startswith(
                ("__Secure-neon-auth.", "__Host-neon-auth.", "neon-auth.", "better-auth.")
            ):
                approved.append(item.strip())
        if approved:
            headers["Cookie"] = "; ".join(approved)
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
    for cookie in upstream.headers.get_list("set-cookie"):
        # The upstream does not set a Domain; preserve its HttpOnly/Secure flags
        # while letting the browser attach the cookie to this first-party host.
        if "domain=" not in cookie.lower():
            response.raw_headers.append((b"set-cookie", cookie.encode("latin-1")))
    response.headers["Cache-Control"] = "no-store"
    return response
