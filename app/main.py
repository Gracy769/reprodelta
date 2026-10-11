"""FastAPI application factory for ReproDelta."""

from __future__ import annotations

import pathlib

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.health import router as health_router
from app.api.investigations import router as investigations_router
from app.api.pages import router as pages_router
from app.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="ReproDelta",
        version=settings.app_version,
        description="Investigates conclusion changes across Python dependency upgrades.",
        docs_url="/docs",
        redoc_url="/redoc",
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

    # Serve compiled frontend assets if the directory exists
    web_dist = pathlib.Path(__file__).parent.parent / "web" / "dist"
    if web_dist.is_dir():
        app.mount("/static", StaticFiles(directory=str(web_dist)), name="static")

    return app


app = create_app()
