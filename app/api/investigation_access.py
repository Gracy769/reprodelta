"""Request-scoped ownership for signed-in users and isolated guest demos."""

import secrets
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import HTTPException, Request, Response

from app.api.connections import current_user
from app.config import get_settings

owner_context: ContextVar[str | None] = ContextVar("investigation_owner", default=None)
_development_key = secrets.token_hex(32)


def cookie_key() -> str:
    settings = get_settings()
    if settings.secret_key == "change-me-in-production":
        if settings.runner_mode == "worker_http" or (
            settings.app_url and settings.app_url.startswith("https://")
        ):
            raise HTTPException(
                503, "Configure a persistent SECRET_KEY before running hosted investigations"
            )
        return _development_key
    if len(settings.secret_key) < 32:
        raise HTTPException(503, "SECRET_KEY must contain at least 32 characters")
    return settings.secret_key


async def investigation_owner(request: Request, response: Response):
    authorization = request.headers.get("authorization")
    session = request.cookies.get("rd_session")
    workbench = request.cookies.get("rd_workbench")
    if authorization or session:
        owner = current_user(authorization or f"Bearer {session}")
    elif workbench:
        try:
            claims = jwt.decode(workbench, cookie_key(), algorithms=["HS256"],
                                issuer="reprodelta-investigation", audience="reprodelta-investigation",
                                options={"require": ["sub", "exp"]})
            owner = claims["sub"]
            if not isinstance(owner, str) or not owner or owner.startswith("guest:"):
                raise ValueError("invalid workbench subject")
        except (jwt.PyJWTError, ValueError) as exc:
            raise HTTPException(401, "Investigation session expired; return to the workspace") from exc
    else:
        token = request.cookies.get("rd_demo")
        owner = None
        if token:
            try:
                claims = jwt.decode(
                    token,
                    cookie_key(),
                    algorithms=["HS256"],
                    issuer="reprodelta-demo",
                    audience="reprodelta-demo",
                    options={"require": ["sub", "exp"]},
                )
                if isinstance(claims["sub"], str) and claims["sub"].startswith("guest:"):
                    owner = claims["sub"]
            except jwt.PyJWTError:
                pass
        if owner is None:
            owner = "guest:" + str(uuid.uuid4())
            token = jwt.encode(
                {
                    "sub": owner,
                    "iss": "reprodelta-demo",
                    "aud": "reprodelta-demo",
                    "exp": datetime.now(UTC) + timedelta(days=7),
                },
                cookie_key(),
                algorithm="HS256",
            )
            response.set_cookie(
                "rd_demo",
                token,
                httponly=True,
                samesite="strict",
                max_age=604800,
                secure=bool(
                    get_settings().app_url and get_settings().app_url.startswith("https://")
                ),
            )
    if request.method not in ("GET", "HEAD"):
        origin = request.headers.get("origin")
        settings = get_settings()
        allowed = {(settings.app_url or str(request.base_url)).rstrip("/")}
        if getattr(settings, "edge_url", None):
            allowed.add(settings.edge_url.rstrip("/"))
        if origin and origin.rstrip("/") not in allowed:
            raise HTTPException(403, "Cross-origin investigation writes are forbidden")
    context = owner_context.set(owner)
    try:
        yield owner
    finally:
        owner_context.reset(context)


def set_workbench_cookie(response: Response, owner: str) -> None:
    """Bridge a verified bearer identity to same-origin investigation navigation."""
    token = jwt.encode({"sub": owner, "iss": "reprodelta-investigation", "aud": "reprodelta-investigation",
                        "exp": datetime.now(UTC) + timedelta(minutes=30)}, cookie_key(), algorithm="HS256")
    response.set_cookie("rd_workbench", token, httponly=True, samesite="strict", max_age=1800,
                        secure=bool(get_settings().app_url and get_settings().app_url.startswith("https://")))


def approval_key(project_id: str) -> str:
    owner = owner_context.get()
    return str(uuid.uuid5(uuid.UUID(project_id), owner)) if owner else project_id
