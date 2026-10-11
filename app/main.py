"""FastAPI application factory for Agent Replay Lab."""

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
        title="Agent Replay Lab",
        version=settings.app_version,
        description="Investigates conclusion changes across Python dependency upgrades.",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.include_router(health_router)
    app.include_router(investigations_router)
    app.include_router(pages_router)

    # Serve compiled frontend assets if the directory exists
    web_dist = pathlib.Path(__file__).parent.parent / "web" / "dist"
    if web_dist.is_dir():
        app.mount("/static", StaticFiles(directory=str(web_dist)), name="static")

    return app


app = create_app()
