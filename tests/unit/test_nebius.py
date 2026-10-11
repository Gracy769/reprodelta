"""
Unit tests for app/providers/nebius.py

Tests cover:
- Mock mode returns ModelProposal with is_mock=True
- Mock mode doesn't require API key
- propose_contract returns proposal with content
- propose_explanation returns proposal mentioning mechanism
- propose_patch returns proposal with diff-like content
- request_digest is a sha256 hex string
"""
from __future__ import annotations

import asyncio
import re

import pytest

from app.providers.nebius import ModelProposal, NebiusClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Basic construction
# ---------------------------------------------------------------------------

class TestNebiusClientConstruction:
    def test_no_api_key_is_mock_mode(self):
        client = NebiusClient(api_key=None)
        assert client.is_mock_mode is True

    def test_with_api_key_is_not_mock_mode(self):
        client = NebiusClient(api_key="sk-test-1234")
        assert client.is_mock_mode is False

    def test_default_model_id(self):
        client = NebiusClient(api_key=None)
        assert "nemotron" in client.model_id.lower() or "nvidia" in client.model_id.lower()


# ---------------------------------------------------------------------------
# Mock mode — propose_contract
# ---------------------------------------------------------------------------

class TestProposeContract:
    @pytest.fixture(autouse=True)
    def client(self):
        self._client = NebiusClient(api_key=None)

    def test_returns_model_proposal(self):
        proposal = run(self._client.propose_contract("Signal classifier demo"))
        assert isinstance(proposal, ModelProposal)

    def test_is_mock_true(self):
        proposal = run(self._client.propose_contract("Signal classifier demo"))
        assert proposal.is_mock is True

    def test_has_response_text(self):
        proposal = run(self._client.propose_contract("Signal classifier demo"))
        assert len(proposal.response_text) > 10

    def test_mock_label_in_response(self):
        proposal = run(self._client.propose_contract("Signal classifier demo"))
        assert "MOCK_LLM_MODE" in proposal.response_text

    def test_mentions_contract_content(self):
        proposal = run(self._client.propose_contract("Signal classifier demo"))
        text = proposal.response_text.lower()
        assert any(kw in text for kw in ("contract", "input", "output", "float32"))

    def test_prompt_template_version(self):
        proposal = run(self._client.propose_contract("Signal classifier demo"))
        assert proposal.prompt_template_version == "contract_v1"

    def test_mock_model_id(self):
        proposal = run(self._client.propose_contract("Signal classifier demo"))
        assert "mock" in proposal.model_id.lower() or "stub" in proposal.model_id.lower()


# ---------------------------------------------------------------------------
# Mock mode — propose_explanation
# ---------------------------------------------------------------------------

class TestProposeExplanation:
    @pytest.fixture(autouse=True)
    def client(self):
        self._client = NebiusClient(api_key=None)

    def test_returns_model_proposal(self):
        proposal = run(self._client.propose_explanation(
            witness_summary="signal changes conclusion near threshold",
            env_diff="NumPy 1.26 → 2.0 NEP 50",
        ))
        assert isinstance(proposal, ModelProposal)

    def test_is_mock_true(self):
        proposal = run(self._client.propose_explanation("w", "e"))
        assert proposal.is_mock is True

    def test_mentions_mechanism(self):
        proposal = run(self._client.propose_explanation(
            witness_summary="signal changes",
            env_diff="NEP 50 change",
        ))
        text = proposal.response_text.lower()
        assert any(kw in text for kw in ("nep", "promotion", "float32", "mechanism", "precision"))

    def test_prompt_template_version(self):
        proposal = run(self._client.propose_explanation("w", "e"))
        assert proposal.prompt_template_version == "explanation_v1"

    def test_deterministic_same_input(self):
        """Same inputs must always produce the same response_text."""
        p1 = run(self._client.propose_explanation("same", "same"))
        p2 = run(self._client.propose_explanation("same", "same"))
        assert p1.response_text == p2.response_text

    def test_request_digest_is_sha256_hex(self):
        proposal = run(self._client.propose_explanation("witness", "env"))
        assert len(proposal.request_digest) == 64
        assert re.fullmatch(r"[0-9a-f]{64}", proposal.request_digest)


# ---------------------------------------------------------------------------
# Mock mode — propose_patch
# ---------------------------------------------------------------------------

class TestProposePatch:
    @pytest.fixture(autouse=True)
    def client(self):
        self._client = NebiusClient(api_key=None)

    def test_returns_model_proposal(self):
        proposal = run(self._client.propose_patch("witness", "intervention", ["pipeline.py"]))
        assert isinstance(proposal, ModelProposal)

    def test_is_mock_true(self):
        proposal = run(self._client.propose_patch("w", "i", ["p.py"]))
        assert proposal.is_mock is True

    def test_has_diff_like_content(self):
        proposal = run(self._client.propose_patch("w", "i", ["p.py"]))
        text = proposal.response_text
        assert "---" in text or "+++" in text or "@@" in text or "diff" in text.lower()

    def test_prompt_template_version(self):
        proposal = run(self._client.propose_patch("w", "i", ["p.py"]))
        assert proposal.prompt_template_version == "patch_v1"

    def test_mock_label_in_response(self):
        proposal = run(self._client.propose_patch("w", "i", ["p.py"]))
        assert "MOCK_LLM_MODE" in proposal.response_text


# ---------------------------------------------------------------------------
# Request digest
# ---------------------------------------------------------------------------

class TestRequestDigest:
    def test_digest_is_hex_sha256(self):
        client = NebiusClient(api_key=None)
        proposal = run(client.propose_explanation("test witness", "test env"))
        assert len(proposal.request_digest) == 64
        assert re.fullmatch(r"[0-9a-f]{64}", proposal.request_digest)

    def test_different_inputs_different_digest(self):
        client = NebiusClient(api_key=None)
        p1 = run(client.propose_explanation("input A", "env A"))
        p2 = run(client.propose_explanation("input B", "env B"))
        assert p1.request_digest != p2.request_digest

    def test_same_inputs_same_digest(self):
        client = NebiusClient(api_key=None)
        p1 = run(client.propose_explanation("same input", "same env"))
        p2 = run(client.propose_explanation("same input", "same env"))
        assert p1.request_digest == p2.request_digest
