"""Integration tests for the FastAPI health endpoint."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import health, investigation_access, investigations
from app.config import Settings
from app.main import app
from app.storage.project_run_queue import ProjectRunQueue


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_health_returns_200():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")
    assert response.status_code == 200


async def test_health_returns_ok_status():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")
    body = response.json()
    assert body["status"] == "ok"


async def test_health_returns_version():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/health")
    body = response.json()
    assert "version" in body
    assert body["version"] == "0.1.0"


async def test_root_returns_html():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


async def test_api_docs_and_brand_assets_are_served_locally():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        docs = await client.get("/docs")
        redoc = await client.get("/redoc")
        logo = await client.get("/assets/favicon.svg")
        swagger_script = await client.get("/assets/api-docs/swagger-ui-bundle.js")
        swagger_style = await client.get("/assets/api-docs/swagger-ui.css")
        redoc_script = await client.get("/assets/api-docs/redoc.standalone.js")

    assert docs.status_code == redoc.status_code == 200
    assert "/assets/favicon.svg" in docs.text
    assert "/assets/api-docs/swagger-ui-bundle.js" in docs.text
    assert "/assets/api-docs/swagger-ui.css" in docs.text
    assert "/assets/api-docs/redoc.standalone.js" in redoc.text
    assert "cdn.jsdelivr.net" not in docs.text + redoc.text
    assert all(
        response.status_code == 200
        for response in (logo, swagger_script, swagger_style, redoc_script)
    )
    assert logo.headers["content-type"].startswith("image/svg+xml")


async def test_ready_requires_live_project_worker_only_when_feature_enabled(monkeypatch):
    class ReadyStore:
        def check_ready(self):
            return None

    monkeypatch.setattr(health, "get_settings", lambda: Settings(
        secret_key="x" * 32, runner_mode="local", e2b_project_runs_enabled=True,
    ))
    monkeypatch.setattr(investigation_access, "cookie_key", lambda: b"x" * 32)
    monkeypatch.setattr(investigations, "get_store", lambda: ReadyStore())
    monkeypatch.setattr(ProjectRunQueue, "has_live_worker", lambda self, kind: False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["project_worker_investigation"] == "unavailable"

    monkeypatch.setattr(ProjectRunQueue, "has_live_worker", lambda self, kind: True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/ready")
    assert response.status_code == 200
    assert response.json()["checks"]["project_worker_investigation"] == "ok"
