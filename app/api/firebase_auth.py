"""Exchange a verified Firebase identity for a ReproDelta session."""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime, timedelta

import httpx
import jwt
from cryptography import x509
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.api.profiles import upsert_profile
from app.config import get_settings

router = APIRouter(prefix="/firebase", include_in_schema=False)
CERT_URL = "https://www.googleapis.com/robot/v1/metadata/x509/securetoken@system.gserviceaccount.com"
_cert_cache: dict[str, str] = {}
_cert_cache_until = 0.0


class TokenExchange(BaseModel):
    id_token: str = Field(min_length=100, max_length=8192)


async def _certificates() -> dict[str, str]:
    global _cert_cache, _cert_cache_until
    if _cert_cache and time.monotonic() < _cert_cache_until:
        return _cert_cache
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
            response = await client.get(CERT_URL)
            response.raise_for_status()
            certs = response.json()
            if not isinstance(certs, dict) or not certs:
                raise ValueError("No verification certificates")
            age = re.search(r"max-age=(\d+)", response.headers.get("cache-control", ""))
            _cert_cache, _cert_cache_until = certs, time.monotonic() + int(age.group(1)) if age else time.monotonic() + 300
            return certs
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(503, "Sign-in verification is temporarily unavailable") from exc


async def _verified_claims(token: str, project_id: str) -> dict:
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            raise ValueError("Invalid token header")
        cert = (await _certificates()).get(header["kid"])
        if not isinstance(cert, str):
            raise ValueError("Unknown signing key")
        key = x509.load_pem_x509_certificate(cert.encode()).public_key()
        claims = jwt.decode(
            token, key, algorithms=["RS256"], audience=project_id,
            issuer=f"https://securetoken.google.com/{project_id}",
            options={"require": ["exp", "iat", "aud", "iss", "sub", "auth_time"]},
        )
        if not claims["sub"] or claims.get("email_verified") is not True:
            raise ValueError("Unverified identity")
        if claims.get("firebase", {}).get("sign_in_provider") not in {"google.com", "password"}:
            raise ValueError("Unsupported sign-in provider")
        if not isinstance(claims["auth_time"], int) or claims["auth_time"] > time.time():
            raise ValueError("Invalid authentication time")
        return claims
    except (jwt.PyJWTError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(401, "Sign-in token is invalid or expired") from exc


@router.post("/session")
async def firebase_session(request: Request, exchange: TokenExchange):
    settings = get_settings()
    if len(settings.secret_key) < 32:
        raise HTTPException(503, "Sign-in is not configured yet")
    origin = request.headers.get("origin")
    if settings.app_url and origin == settings.app_url.rstrip("/"):
        project_id = settings.firebase_project_id
    elif getattr(settings, "edge_url", None) and origin == settings.edge_url.rstrip("/"):
        project_id = getattr(settings, "firebase_edge_project_id", None)
    else:
        raise HTTPException(403, "Origin is not allowed")
    if not project_id:
        raise HTTPException(503, "Sign-in is not configured yet")
    claims = await _verified_claims(exchange.id_token, project_id)
    now = datetime.now(UTC)
    name = claims.get("name") or claims.get("email") or "User"
    # Preserve existing account IDs for the original Firebase project.
    subject = (f"firebase:{claims['sub']}" if project_id == settings.firebase_project_id
               else f"firebase:{project_id}:{claims['sub']}")
    upsert_profile(subject, name, claims.get("email"))
    session = jwt.encode(
        {"sub": subject, "name": name, "iss": "reprodelta", "aud": "reprodelta",
         "iat": now, "exp": now + timedelta(days=7)},
        settings.secret_key, algorithm="HS256",
    )
    response = JSONResponse({"ok": True})
    response.set_cookie("rd_session", session, httponly=True, secure=True, samesite="lax", max_age=604800, path="/")
    response.headers["Cache-Control"] = "no-store"
    return response
