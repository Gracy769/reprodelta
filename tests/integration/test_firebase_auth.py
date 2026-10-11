"""Firebase tokens must be signed for this project and come from Google sign-in."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import jwt
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi import HTTPException

from app.api import firebase_auth
from app.main import app


@pytest.mark.asyncio
async def test_firebase_verifies_signature_project_and_google_provider(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(UTC)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Firebase test")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
            .sign(key, hashes.SHA256()))
    from cryptography.hazmat.primitives.serialization import Encoding

    async def certs():
        return {"test-key": cert.public_bytes(Encoding.PEM).decode()}

    monkeypatch.setattr(firebase_auth, "_certificates", certs)
    claims = {"sub": "uid-123", "aud": "reprodelta-firebase", "iss": "https://securetoken.google.com/reprodelta-firebase",
              "exp": now + timedelta(minutes=5), "iat": now - timedelta(minutes=1),
              "auth_time": int(now.timestamp()) - 60, "email_verified": True,
              "firebase": {"sign_in_provider": "google.com"}}

    def signed(payload):
        return jwt.encode(payload, key, algorithm="RS256", headers={"kid": "test-key"})

    assert (await firebase_auth._verified_claims(signed(claims), "reprodelta-firebase"))["sub"] == "uid-123"
    assert (await firebase_auth._verified_claims(
        signed({**claims, "firebase": {"sign_in_provider": "password"}}),
        "reprodelta-firebase",
    ))["sub"] == "uid-123"
    with pytest.raises(HTTPException):
        await firebase_auth._verified_claims(signed(claims), "another-project")
    with pytest.raises(HTTPException):
        await firebase_auth._verified_claims(signed({**claims, "email_verified": False}), "reprodelta-firebase")
    with pytest.raises(HTTPException):
        await firebase_auth._verified_claims(signed({**claims, "firebase": {"sign_in_provider": "anonymous"}}), "reprodelta-firebase")


@pytest.mark.asyncio
async def test_firebase_exchange_checks_origin_and_sets_http_only_cookie(monkeypatch):
    monkeypatch.setattr(firebase_auth, "get_settings", lambda: SimpleNamespace(
        firebase_project_id="reprodelta-firebase", secret_key="test-secret-that-is-long-enough-for-hs256",
        app_url="https://reprodelta.example",
    ))

    async def verified(_token, _project):
        return {"sub": "uid-123", "name": "Ada"}

    monkeypatch.setattr(firebase_auth, "_verified_claims", verified)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://reprodelta.example") as client:
        rejected = await client.post("/firebase/session", json={"id_token": "x" * 100}, headers={"Origin": "https://evil.example"})
        accepted = await client.post("/firebase/session", json={"id_token": "x" * 100}, headers={"Origin": "https://reprodelta.example"})
    assert rejected.status_code == 403
    assert accepted.status_code == 200
    assert "HttpOnly" in accepted.headers["set-cookie"]
    assert "Secure" in accepted.headers["set-cookie"]
