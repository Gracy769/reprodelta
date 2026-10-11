"""FastAPI application factory for ReproDelta."""

from __future__ import annotations

import pathlib

from fastapi import FastAPI, Request
from fastapi.openapi.docs import get_redoc_html
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.api.health import router as health_router
from app.api.investigations import router as investigations_router
from app.api.pages import router as pages_router
from app.api.pages import templates
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

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        )
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    app.include_router(health_router)
    app.include_router(investigations_router)
    app.include_router(pages_router)

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
