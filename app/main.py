"""FastAPI application factory for ReproDelta."""

from __future__ import annotations

import pathlib

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.openapi.docs import get_redoc_html
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.account_pages import router as account_pages_router
from app.api.auth_bridge import router as auth_bridge_router
from app.api.connections import router as connections_router
from app.api.firebase_auth import router as firebase_auth_router
from app.api.health import router as health_router
from app.api.investigations import router as investigations_router
from app.api.pages import router as pages_router
from app.api.pages import templates
from app.api.project_profiles import router as project_profiles_router
from app.api.source_oauth import router as source_oauth_router
from app.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="ReproDelta",
        version=settings.app_version,
        description="Investigates conclusion changes across Python dependency upgrades.",
        docs_url=None,
        redoc_url=None,
    )

    @app.exception_handler(StarletteHTTPException)
    async def not_found_page(request: Request, exc: StarletteHTTPException):
        """Show a branded page for browser navigation; preserve JSON API errors."""
        if (
            exc.status_code == 404
            and request.method in {"GET", "HEAD"}
            and "text/html" in request.headers.get("accept", "")
            and not request.url.path.startswith(("/api/", "/auth/", "/oauth/", "/assets/"))
        ):
            return templates.TemplateResponse(request, "not_found.html", status_code=404)
        return await http_exception_handler(request, exc)

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        auth_connect = ""
        if settings.neon_auth_base_url:
            from urllib.parse import urlsplit

            parts = urlsplit(settings.neon_auth_base_url)
            if parts.scheme == "https" and parts.hostname:
                auth_connect = f" https://{parts.hostname}"
        firebase_frame = ""
        if settings.firebase_auth_domain:
            from urllib.parse import urlsplit

            domain = urlsplit("https://" + settings.firebase_auth_domain).hostname
            if domain and domain == settings.firebase_auth_domain and domain.endswith((".firebaseapp.com", ".web.app")):
                firebase_frame = f" https://{domain}"
        firebase_connect = " https://identitytoolkit.googleapis.com https://securetoken.googleapis.com" if settings.firebase_project_id else ""
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline' https://apis.google.com; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            f"connect-src 'self'{auth_connect}{firebase_connect}{firebase_frame}; "
            f"frame-src 'self' https://accounts.google.com{firebase_frame}; "
            "object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        )
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
        response.headers["Referrer-Policy"] = "same-origin"
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-store"
        if request.url.path.startswith(("/api/connections", "/api/investigations")):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(health_router)
    app.include_router(investigations_router)
    app.include_router(pages_router)
    app.include_router(account_pages_router)
    app.include_router(auth_bridge_router)
    app.include_router(source_oauth_router)
    app.include_router(firebase_auth_router)
    app.include_router(connections_router)
    app.include_router(project_profiles_router)

    @app.get("/docs", response_class=HTMLResponse, include_in_schema=False)
    async def custom_swagger_ui_html(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "api_docs.html",
            {"openapi_url": app.openapi_url},
        )

    @app.get("/redoc", include_in_schema=False)
    async def custom_redoc_html() -> HTMLResponse:
        return get_redoc_html(
            openapi_url=app.openapi_url,
            title="ReproDelta — API reference",
            redoc_js_url="/assets/api-docs/redoc.standalone.js",
            redoc_favicon_url="/assets/favicon.svg",
            with_google_fonts=False,
        )

    app.mount(
        "/assets",
        StaticFiles(directory=str(pathlib.Path(__file__).parent / "static")),
        name="assets",
    )

    # Serve compiled frontend assets if the directory exists
    web_dist = pathlib.Path(__file__).parent.parent / "web" / "dist"
    if web_dist.is_dir():
        app.mount("/static", StaticFiles(directory=str(web_dist)), name="static")

    return app


app = create_app()
