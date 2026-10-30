"""Security hardening regression tests.

Verifies:
1. AST validation prevents sequence multiplication and oversized strings (DoS protection).
2. Cipher derives tokens using HKDF-SHA256 with legacy backward-compatibility (Cryptographic defense).
3. Investigation access prevents unauthorized cross-owner read access (BOLA defense).
"""

from __future__ import annotations

import base64
import hashlib
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException

from app.api import connections, investigations
from app.api.investigation_access import owner_context
from app.storage.investigation_store import MemoryInvestigationStore
from runner.patching import validate_numerical_source

ORIGINAL_PIPELINE = """
import numpy as np

THRESHOLD = 0.5

def classify_signal(signal: list[float]) -> dict:
    ratio = compute_power_ratio(signal)
    return {"classified": ratio > THRESHOLD}

def compute_power_ratio(signal: list[float]) -> float:
    return float(np.sum(np.array(signal, dtype=np.float32)))
"""


def test_validate_numerical_source_blocks_sequence_multiplication():
    patched_seq_mult = """
import numpy as np

THRESHOLD = 0.5

def classify_signal(signal: list[float]) -> dict:
    bomb = [0] * 1000
    ratio = compute_power_ratio(signal)
    return {"classified": ratio > THRESHOLD}

def compute_power_ratio(signal: list[float]) -> float:
    return float(np.sum(np.array(signal, dtype=np.float32)))
"""
    with pytest.raises(ValueError, match="Sequence multiplication is forbidden"):
        validate_numerical_source(ORIGINAL_PIPELINE, patched_seq_mult)


def test_validate_numerical_source_blocks_oversized_string():
    patched_oversized_str = f"""
import numpy as np

THRESHOLD = 0.5

def classify_signal(signal: list[float]) -> dict:
    comment = "{'a' * 1005}"
    ratio = compute_power_ratio(signal)
    return {{"classified": ratio > THRESHOLD}}

def compute_power_ratio(signal: list[float]) -> float:
    return float(np.sum(np.array(signal, dtype=np.float32)))
"""
    with pytest.raises(ValueError, match="Oversized string literal"):
        validate_numerical_source(ORIGINAL_PIPELINE, patched_oversized_str)


def test_connections_cipher_hkdf_and_legacy_compatibility(monkeypatch):
    test_secret = "test-secret-key-that-is-at-least-32-chars-long"
    monkeypatch.setattr(
        connections, "get_settings", lambda: SimpleNamespace(secret_key=test_secret)
    )

    # Encrypt with legacy single-round unsalted SHA-256 Fernet key
    legacy_key = base64.urlsafe_b64encode(hashlib.sha256(test_secret.encode()).digest())
    legacy_fernet = Fernet(legacy_key)
    legacy_token = legacy_fernet.encrypt(b"legacy-oauth-token").decode()

    # Verify modern _cipher can decrypt legacy tokens
    cipher = connections._cipher()
    assert cipher.decrypt(legacy_token.encode()).decode() == "legacy-oauth-token"

    # Verify modern _cipher encrypts with primary key and can decrypt it
    new_token = cipher.encrypt(b"modern-oauth-token").decode()
    assert cipher.decrypt(new_token.encode()).decode() == "modern-oauth-token"


def test_investigation_bola_rejection():
    store = MemoryInvestigationStore()
    inv_id = store.create_investigation(
        "00000000-0000-0000-0000-000000000001",
        {},
        owner_id="github:authorized-user",
    )

    previous_store = investigations.get_store()
    investigations.set_store(store)
    try:
        # Context matches owner
        token = owner_context.set("github:authorized-user")
        try:
            record = investigations._get_investigation_or_404(inv_id)
            assert record["id"] == inv_id
        finally:
            owner_context.reset(token)

        # Context belongs to different user -> 404
        token = owner_context.set("guest:unauthorized-user")
        try:
            with pytest.raises(HTTPException) as exc_info:
                investigations._get_investigation_or_404(inv_id)
            assert exc_info.value.status_code == 404
        finally:
            owner_context.reset(token)
    finally:
        investigations.set_store(previous_store)
