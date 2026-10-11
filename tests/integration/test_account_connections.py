"""Account pages and bounded source-host review behavior."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import HTTPException

from app.api import auth_bridge, connections
from app.main import app


@pytest.mark.asyncio
async def test_account_pages_expose_real_forms_and_assets():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        signup = await client.get("/signup")
        signin = await client.get("/signin")
        workspace = await client.get("/workspace")
        css = await client.get("/assets/auth.css")
        js = await client.get("/assets/workspace.js")
    assert signup.status_code == signin.status_code == workspace.status_code == 200
    assert "Create your account" in signup.text
    assert 'autocomplete="new-password"' in signup.text
    assert 'autocomplete="current-password"' in signin.text
    assert "Connect GitHub" in workspace.text
    assert "never sent to the runner" in workspace.text
    assert css.status_code == js.status_code == 200


@pytest.mark.asyncio
async def test_auth_bridge_rejects_other_origins_and_unlisted_paths(monkeypatch):
    monkeypatch.setattr(
        auth_bridge,
        "get_settings",
        lambda: SimpleNamespace(
            neon_auth_base_url="https://auth.example.test", app_url=None, auth_allowed_origin=None
        ),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8002") as client:
        unknown = await client.get("/auth/admin/users")
        wrong_origin = await client.post(
            "/auth/sign-in/email",
            headers={"Origin": "https://evil.example"},
            json={"email": "a@example.com", "password": "password123"},
        )
    assert unknown.status_code == 404
    assert wrong_origin.status_code == 403


@pytest.mark.asyncio
async def test_auth_bridge_preserves_http_only_session_cookie(monkeypatch):
    monkeypatch.setattr(
        auth_bridge,
        "get_settings",
        lambda: SimpleNamespace(
            neon_auth_base_url="https://auth.example.test",
            app_url="https://internal.local",
            auth_allowed_origin="https://reprodelta-lab.antideploy.app",
        ),
    )

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def request(self, method, url, **kwargs):
            assert url == "https://auth.example.test/sign-in/email"
            assert kwargs["headers"]["Origin"] == "https://reprodelta-lab.antideploy.app"
            assert kwargs["headers"]["Cookie"] == "__Secure-neon-auth.session_token=opaque"
            return httpx.Response(
                200,
                headers={
                    "set-cookie": "__Secure-neon-auth.session_token=opaque; Path=/; HttpOnly; Secure; SameSite=None"
                },
                json={"user": {"id": "u1"}},
            )

    original_client = auth_bridge.httpx.AsyncClient
    transport = httpx.ASGITransport(app=app)
    async with original_client(transport=transport, base_url="http://internal.local") as client:
        monkeypatch.setattr(auth_bridge.httpx, "AsyncClient", lambda **kwargs: FakeClient())
        response = await client.post(
            "/auth/sign-in/email",
            headers={
                "Origin": "https://reprodelta-lab.antideploy.app",
                "Cookie": "other-secret=hidden; __Secure-neon-auth.session_token=opaque",
            },
            json={"email": "a@example.com", "password": "password123"},
        )
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_social_signin_and_verifier_exchange_stay_first_party(monkeypatch):
    monkeypatch.setattr(
        auth_bridge,
        "get_settings",
        lambda: SimpleNamespace(
            neon_auth_base_url="https://auth.example.test/neondb/auth",
            app_url="https://reprodelta-lab.antideploy.app",
            auth_allowed_origin="https://reprodelta-lab.antideploy.app",
        ),
    )

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def request(self, method, url, **kwargs):
            assert method == "POST"
            assert url.endswith("/sign-in/social")
            return httpx.Response(
                200,
                headers={"set-cookie": "__Secure-neon-auth.session_challenge=proof; Path=/; HttpOnly; Secure; SameSite=None; Partitioned"},
                json={"url": "https://provider.example/authorize"},
            )

        async def get(self, url, **kwargs):
            assert url.endswith("/get-session")
            assert kwargs["params"] == {"neon_auth_session_verifier": "one-time-code"}
            assert kwargs["headers"]["Cookie"] == "__Secure-neon-auth.session_challenge=proof"
            return httpx.Response(
                200,
                headers={"set-cookie": "__Secure-neon-auth.session_token=opaque; Path=/; HttpOnly; Secure; SameSite=None; Partitioned"},
                json={"user": {"id": "u1"}},
            )

    original_client = auth_bridge.httpx.AsyncClient
    transport = httpx.ASGITransport(app=app)
    async with original_client(transport=transport, base_url="https://reprodelta-lab.antideploy.app") as client:
        monkeypatch.setattr(auth_bridge.httpx, "AsyncClient", lambda **kwargs: FakeClient())
        start = await client.post(
            "/auth/sign-in/social",
            headers={"Origin": "https://reprodelta-lab.antideploy.app"},
            json={"provider": "google", "callbackURL": "https://reprodelta-lab.antideploy.app/workspace"},
        )
        finish = await client.get(
            "/?neon_auth_session_verifier=one-time-code",
            headers={"Cookie": "__Secure-neon-auth.session_challenge=proof"},
            follow_redirects=False,
        )
        workspace_finish = await client.get(
            "/workspace?neon_auth_session_verifier=one-time-code",
            headers={"Cookie": "__Secure-neon-auth.session_challenge=proof"},
            follow_redirects=False,
        )
    assert start.status_code == 200
    assert "SameSite=Lax" in start.headers["set-cookie"]
    assert "Partitioned" not in start.headers["set-cookie"]
    assert finish.status_code == 303
    assert finish.headers["location"] == "/workspace"
    assert "session_token=opaque" in finish.headers["set-cookie"]
    assert "SameSite=Lax" in finish.headers["set-cookie"]
    assert workspace_finish.status_code == 303
    assert workspace_finish.headers["location"] == "/workspace"


@pytest.mark.asyncio
async def test_verifier_without_matching_challenge_fails_closed(monkeypatch):
    monkeypatch.setattr(
        auth_bridge,
        "get_settings",
        lambda: SimpleNamespace(neon_auth_base_url="https://auth.example.test"),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://reprodelta-lab.antideploy.app") as client:
        response = await client.get("/?neon_auth_session_verifier=unmatched", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/signin?auth_error=oauth"


def test_signed_neon_jwt_required_for_private_source_routes(monkeypatch):
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    base = "https://auth.example.test/project/auth"
    monkeypatch.setattr(
        connections,
        "get_settings",
        lambda: SimpleNamespace(neon_auth_base_url=base, neon_auth_jwks_url=base + "/jwks"),
    )
    monkeypatch.setattr(
        connections,
        "_jwks_client",
        lambda url: SimpleNamespace(
            get_signing_key_from_jwt=lambda token: SimpleNamespace(key=public_key)
        ),
    )
    token = jwt.encode(
        {
            "sub": "user-42",
            "iss": base,
            "aud": base,
            "exp": datetime.now(UTC) + timedelta(minutes=5),
        },
        private_key,
        algorithm="EdDSA",
    )
    assert connections.current_user("Bearer " + token) == "user-42"
    with pytest.raises(HTTPException) as missing:
        connections.current_user(None)
    assert missing.value.status_code == 401
    with pytest.raises(HTTPException) as invalid:
        connections.current_user("Bearer not-a-token")
    assert invalid.value.status_code == 401


@pytest.mark.asyncio
async def test_repository_path_cannot_escape_fixed_host_api(monkeypatch):
    monkeypatch.setattr(connections, "_saved_token", lambda owner, provider: "test-token")
    with pytest.raises(HTTPException) as exc:
        await connections.changes("github", "owner/repo/../../user", "user-42")
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_advice_is_explicitly_metadata_only(monkeypatch):
    monkeypatch.setattr(connections, "_saved_token", lambda owner, provider: "test-token")

    async def fake_get(provider, token, path, params=None):
        return [{"filename": "requirements.txt", "status": "modified", "patch": "+numpy==2.0.2"}]

    monkeypatch.setattr(connections, "_host_get", fake_get)
    result = await connections.change_advice("github", "lab/project", 7, "user-42")
    assert result["mode"] == "metadata_review_only"
    assert result["executed_repository_code"] is False
    assert result["numpy_mentioned"] is True
    assert result["dependency_files"] == ["requirements.txt"]
