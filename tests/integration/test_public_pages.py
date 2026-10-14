"""Public navigation stays useful without changing API error semantics."""

from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import account_pages, pages
from app.main import create_app


@pytest.mark.asyncio
async def test_missing_browser_route_gets_branded_404():
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        page = await client.get("/missing-route", headers={"accept": "text/html"})
        api = await client.get("/api/does-not-exist", headers={"accept": "text/html"})
    assert page.status_code == 404
    assert "Lost in the codebase?" in page.text
    assert "curious robot" in page.text
    assert "Return home" in page.text
    assert api.status_code == 404
    assert "application/json" in api.headers["content-type"]


@pytest.mark.asyncio
async def test_public_site_metadata_lists_only_public_pages():
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        robots = await client.get("/robots.txt")
        sitemap = await client.get("/sitemap.xml")
    assert robots.status_code == sitemap.status_code == 200
    assert "text/plain" in robots.headers["content-type"]
    assert "Disallow: /workspace" in robots.text
    assert "Sitemap: https://reprodelta-web-production.up.railway.app/sitemap.xml" in robots.text
    assert "application/xml" in sitemap.headers["content-type"]
    assert "https://reprodelta-web-production.up.railway.app/demo/investigation" in sitemap.text
    assert "/workspace" not in sitemap.text


@pytest.mark.asyncio
async def test_investigation_start_is_explicit_and_demo_has_no_signal_table():
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        workspace = await client.get("/workspace")
        demo = await client.get("/demo/investigation")
    assert 'id="start-investigation-btn"' in workspace.text
    assert 'id="chosen-change"' in workspace.text
    assert "Start live investigation" in demo.text
    assert "Try your own eight-value signal" in demo.text
    assert 'id="signal-inputs"' not in demo.text


@pytest.mark.asyncio
async def test_server_secrets_do_not_render_in_public_pages(monkeypatch):
    settings = SimpleNamespace(
        neon_auth_base_url=None,
        gitlab_oauth_client_id="public-id",
        gitlab_oauth_client_secret="DO_NOT_RENDER_GITLAB_SECRET",
        github_oauth_client_secret="DO_NOT_RENDER_GITHUB_SECRET",
        nebius_api_key="DO_NOT_RENDER_NEBIUS_KEY",
        secret_key="DO_NOT_RENDER_SESSION_KEY",
        firebase_api_key="public-firebase-identifier",
        firebase_auth_domain="example.firebaseapp.com",
        firebase_project_id="example",
        firebase_app_id="public-app-id",
    )
    monkeypatch.setattr(account_pages, "get_settings", lambda: settings)
    monkeypatch.setattr(pages, "get_settings", lambda: settings)
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        pages_to_check = [await client.get(path) for path in ("/", "/signin", "/signup", "/workspace", "/demo/investigation")]
    for response in pages_to_check:
        assert response.status_code == 200
        assert "DO_NOT_RENDER_" not in response.text
    assert "public-firebase-identifier" in pages_to_check[1].text
