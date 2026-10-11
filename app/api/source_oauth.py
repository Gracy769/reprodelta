"""First-party OAuth login with server-side source-host token storage.

OAuth credentials and access tokens never reach browser JavaScript. The browser
receives a short lived signed identity token for read-only API requests.
"""

from __future__ import annotations

import hmac
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
import jwt
import psycopg
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.api.connections import _cipher, _database_dsn, _ensure_table
from app.config import get_settings

router = APIRouter(prefix="/oauth", include_in_schema=False)

PROVIDERS = {
    "github": {
        "authorize": "https://github.com/login/oauth/authorize",
        "token": "https://github.com/login/oauth/access_token",
        "user": "https://api.github.com/user",
        # OAuth Apps cannot request private-repo read-only access. ReproDelta only
        # performs GET requests; the broad GitHub repo grant is disclosed in UI.
        "scope": "read:user repo",
    },
    "gitlab": {
        "authorize": "https://gitlab.com/oauth/authorize",
        "token": "https://gitlab.com/oauth/token",
        "user": "https://gitlab.com/api/v4/user",
        "scope": "read_api",
    },
}


def _credentials(provider: str) -> tuple[str, str]:
    settings = get_settings()
    if provider not in PROVIDERS:
        raise HTTPException(404, "Provider not supported")
    client_id = getattr(settings, f"{provider}_oauth_client_id")
    client_secret = getattr(settings, f"{provider}_oauth_client_secret")
    if not client_id or not client_secret or len(settings.secret_key) < 32 or settings.secret_key == "change-me-in-production":
        raise HTTPException(503, f"{provider.title()} sign-in is not configured yet")
    return client_id, client_secret


def _callback(provider: str) -> str:
    base = get_settings().app_url
    if not base or not base.startswith("https://"):
        raise HTTPException(503, "Set APP_URL to the public HTTPS address")
    return f"{base.rstrip('/')}/oauth/{provider}/callback"


def _safe_next(path: str) -> str:
    return path if path in {"/workspace", "/investigations/new"} else "/investigations/new"


def _session_claims(token: str | None) -> dict:
    if not token or len(get_settings().secret_key) < 32:
        raise HTTPException(401, "Sign in to continue")
    try:
        return jwt.decode(
            token, get_settings().secret_key, algorithms=["HS256"],
            issuer="reprodelta", audience="reprodelta",
            options={"require": ["sub", "name", "exp"]},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(401, "Session expired") from exc


@router.get("/{provider}/start")
async def oauth_start(provider: str, request: Request, next: str = "/investigations/new", link: bool = False):
    client_id, _ = _credentials(provider)
    link_owner = _session_claims(request.cookies.get("rd_session"))["sub"] if link else None
    nonce = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    challenge = jwt.encode(
        {"state": nonce, "next": _safe_next(next), "link_owner": link_owner, "iss": "reprodelta-oauth", "exp": now + timedelta(minutes=10)},
        get_settings().secret_key,
        algorithm="HS256",
    )
    params = {"client_id": client_id, "redirect_uri": _callback(provider), "scope": PROVIDERS[provider]["scope"], "state": nonce}
    if provider == "gitlab":
        params["response_type"] = "code"
    response = RedirectResponse(PROVIDERS[provider]["authorize"] + "?" + urlencode(params), status_code=303)
    response.set_cookie(f"rd_oauth_{provider}", challenge, httponly=True, secure=True, samesite="lax", max_age=600, path=f"/oauth/{provider}/callback")
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/{provider}/callback")
async def oauth_callback(provider: str, request: Request, code: str = "", state: str = ""):
    client_id, client_secret = _credentials(provider)
    challenge = request.cookies.get(f"rd_oauth_{provider}")
    if not challenge or not code or not state:
        raise HTTPException(400, "OAuth response is incomplete")
    try:
        claims = jwt.decode(challenge, get_settings().secret_key, algorithms=["HS256"], issuer="reprodelta-oauth")
    except jwt.PyJWTError as exc:
        raise HTTPException(400, "OAuth request expired") from exc
    if not hmac.compare_digest(state, str(claims.get("state", ""))):
        raise HTTPException(400, "OAuth request did not match")
    payload = {"client_id": client_id, "client_secret": client_secret, "code": code, "redirect_uri": _callback(provider)}
    if provider == "gitlab":
        payload["grant_type"] = "authorization_code"
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=False) as client:
            token_response = await client.post(PROVIDERS[provider]["token"], data=payload, headers={"Accept": "application/json"})
            token_response.raise_for_status()
            token_data = token_response.json()
            access_token = token_data.get("access_token")
            if not isinstance(access_token, str) or not access_token:
                raise ValueError("Missing OAuth token")
            if provider == "gitlab":
                refresh_token = token_data.get("refresh_token")
                expires_in = token_data.get("expires_in")
                if not isinstance(refresh_token, str) or not refresh_token:
                    raise ValueError("Missing GitLab refresh token")
                if not isinstance(expires_in, int) or isinstance(expires_in, bool) or expires_in <= 0:
                    raise ValueError("Missing GitLab token expiry")
            user_response = await client.get(PROVIDERS[provider]["user"], headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"})
            user_response.raise_for_status()
            user = user_response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, f"{provider.title()} sign-in failed") from exc
    user_id = user.get("id")
    source_account = user.get("login" if provider == "github" else "username")
    if not isinstance(user_id, int) or not isinstance(source_account, str) or not source_account:
        raise HTTPException(502, "Provider identity was incomplete")
    owner_id, account = f"{provider}:{user_id}", source_account
    link_owner = claims.get("link_owner")
    if link_owner:
        prior = _session_claims(request.cookies.get("rd_session"))
        if prior["sub"] != link_owner:
            raise HTTPException(401, "Account changed during connection")
        owner_id, account = prior["sub"], prior["name"]
    with psycopg.connect(_database_dsn()) as conn:
        _ensure_table(conn)
        conn.execute(
            """INSERT INTO connected_source_accounts
               (owner_id, provider, account_name, encrypted_token, encrypted_refresh_token, token_expires_at, connected_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (owner_id, provider) DO UPDATE SET account_name=EXCLUDED.account_name,
                   encrypted_token=EXCLUDED.encrypted_token,
                   encrypted_refresh_token=EXCLUDED.encrypted_refresh_token,
                   token_expires_at=EXCLUDED.token_expires_at,
                   connected_at=EXCLUDED.connected_at""",
            (owner_id, provider, source_account,
             _cipher().encrypt(access_token.encode()).decode(),
             _cipher().encrypt(refresh_token.encode()).decode() if provider == "gitlab" else None,
             datetime.now(UTC) + timedelta(seconds=expires_in) if provider == "gitlab" else None,
             datetime.now(UTC)),
        )
    now = datetime.now(UTC)
    session = jwt.encode(
        {"sub": owner_id, "name": account, "iss": "reprodelta", "aud": "reprodelta", "iat": now, "exp": now + timedelta(days=7)},
        get_settings().secret_key,
        algorithm="HS256",
    )
    response = RedirectResponse(_safe_next(str(claims.get("next", ""))), status_code=303)
    response.delete_cookie(f"rd_oauth_{provider}", path=f"/oauth/{provider}/callback")
    response.set_cookie("rd_session", session, httponly=True, secure=True, samesite="lax", max_age=604800, path="/")
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/session")
async def oauth_session(request: Request):
    token = request.cookies.get("rd_session")
    claims = _session_claims(token)
    response = JSONResponse({"user": {"name": claims["name"], "provider": claims["sub"].split(":", 1)[0]}, "token": token})
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/signout")
async def oauth_signout(request: Request):
    origin = request.headers.get("origin", "")
    if origin != get_settings().app_url:
        raise HTTPException(403, "Origin is not allowed")
    response = JSONResponse({"ok": True})
    response.delete_cookie("rd_session", path="/")
    response.headers["Cache-Control"] = "no-store"
    return response
