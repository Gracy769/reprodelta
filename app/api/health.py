"""Health check router."""

from __future__ import annotations

from fastapi import APIRouter

from app.config import get_settings

router = APIRouter()


@router.get("/health", tags=["ops"])
async def health() -> dict:
    """Return service liveness status and version."""
    settings = get_settings()
    return {"status": "ok", "version": settings.app_version}
