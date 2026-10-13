"""
Nebius Token Factory client for NVIDIA Nemotron model.
Uses the OpenAI-compatible Nebius Token Factory API.

When NEBIUS_API_KEY is not set, runs in mock mode with canned responses.
All model output is treated as untrusted data and validated by callers.

Reference: https://api.tokenfactory.nebius.com/docs
"""
from __future__ import annotations

import hashlib
import pathlib
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

# ---------------------------------------------------------------------------
# Mock responses (deterministic, NOT random)
# ---------------------------------------------------------------------------

MOCK_RESPONSES: dict[str, str] = {
    "contract_proposal": """Based on the signal classification pipeline, I propose this contract:
- Input: 1D float32 signal with 8 elements, values in [-10, 10]
- Output: conclusion field (above_threshold / below_threshold) 
- Decision rule: power ratio > 0.5
- Assumption: NEP 50 changes float32 arithmetic precision near threshold
- Provenance: mock_contract_proposer_v1
[MOCK_LLM_MODE - NEBIUS_API_KEY not set]""",

    "explanation": """Proposed mechanism: NumPy 2.0 NEP 50 changes scalar type promotion.
When a float32 scalar is divided by a Python float (2.0), NumPy 1.26 promotes to float64
(higher precision), while NumPy 2.0 stays float32 (lower precision).
Near the classification threshold, this precision difference can change the conclusion.
Falsifiable prediction: an explicit float32 division (np.float32(2.0)) in NumPy 1.26 
would reproduce the NumPy 2.0 result.
[MOCK_LLM_MODE - NEBIUS_API_KEY not set]""",

    "patch_proposal": """Proposed fix: explicitly cast the divisor to float32 before division,
ensuring consistent intermediate dtype in both NumPy versions.

--- a/fixture/project/pipeline.py
+++ b/fixture/project/pipeline.py
@@ -36 +36 @@
-    normalized = np.array([sample / 2.0 for sample in signal])
+    normalized = np.array([sample / np.float32(2.0) for sample in signal])
@@ -69 +69 @@
-    normalized = np.array([sample / 2.0 for sample in signal])
+    normalized = np.array([sample / np.float32(2.0) for sample in signal])

[MOCK_LLM_MODE - NEBIUS_API_KEY not set]""",
}

# ---------------------------------------------------------------------------
# Prompt templates directory
# ---------------------------------------------------------------------------

_PROMPTS_DIR = pathlib.Path(__file__).parent / "prompts"


def _load_prompt(name: str) -> str:
    """Load a versioned prompt template from the prompts directory."""
    path = _PROMPTS_DIR / name
    if path.exists():
        return path.read_text(encoding="utf-8")
    return f"[prompt template not found: {name}]"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class ModelProposal(BaseModel):
    """A single proposal from the Nebius/Nemotron model."""

    model_config = ConfigDict(frozen=True)

    model_id: str
    prompt_template_version: str
    request_digest: str  # sha256 hex of (system_prompt + user_message)
    response_text: str
    is_mock: bool
    created_at: datetime


# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------


class NebiusError(RuntimeError):
    """Raised on HTTP or API errors from the Nebius API."""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class NebiusClient:
    """
    Client for the Nebius Token Factory / Nemotron model.

    Parameters
    ----------
    api_key:
        The Nebius API key. If ``None`` the client runs in mock mode.
    model_id:
        The model identifier to use for live calls.
    """

    _API_BASE = "https://api.tokenfactory.nebius.com/v1"

    def __init__(
        self,
        api_key: str | None,
        model_id: str = "nvidia/Nemotron-3_5-Lightning",
        api_base: str = _API_BASE,
    ) -> None:
        self._api_key = api_key
        self.model_id = model_id
        self._api_base = api_base.rstrip("/")

    @property
    def is_mock_mode(self) -> bool:
        return not bool(self._api_key)

    @property
    def api_base(self) -> str:
        """Return the base URL for the Nebius API."""
        return self._api_base

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _request_digest(self, system_prompt: str, user_message: str) -> str:
        combined = (system_prompt + user_message).encode("utf-8")
        return hashlib.sha256(combined).hexdigest()

    def _mock_proposal(
        self,
        key: Literal["contract_proposal", "explanation", "patch_proposal"],
        system_prompt: str,
        user_message: str,
        template_version: str,
    ) -> ModelProposal:
        return ModelProposal(
            model_id="mock/nemotron-stub",
            prompt_template_version=template_version,
            request_digest=self._request_digest(system_prompt, user_message),
            response_text=MOCK_RESPONSES[key],
            is_mock=True,
            created_at=datetime.now(UTC),
        )

    def _live_call(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: int,
        template_version: str,
    ) -> ModelProposal:
        """Make a live HTTP call to the Nebius API."""
        import json as _json
        import urllib.error
        import urllib.request

        url = f"{self._api_base}/chat/completions"
        payload = _json.dumps({
            "model": self.model_id,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "max_tokens": max_tokens,
        }).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = _json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise NebiusError(
                f"Nebius API HTTP {exc.code}: {exc.reason}"
            ) from exc
        except Exception as exc:
            raise NebiusError(f"Nebius API call failed: {exc}") from exc

        try:
            response_text = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise NebiusError(f"Unexpected Nebius response shape: {body!r}") from exc

        return ModelProposal(
            model_id=self.model_id,
            prompt_template_version=template_version,
            request_digest=self._request_digest(system_prompt, user_message),
            response_text=f"[LIVE - {self.model_id}]\n{response_text}",
            is_mock=False,
            created_at=datetime.now(UTC),
        )

    async def check_available_models(self) -> list[str]:
        """
        Query the Nebius models endpoint and return available model IDs.

        Returns an empty list in mock mode.
        Raises NebiusError on HTTP failure.
        """
        if self.is_mock_mode:
            return []

        import json as _json
        import urllib.error
        import urllib.request

        url = f"{self._api_base}/models"
        req = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = _json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise NebiusError(
                f"Nebius models endpoint HTTP {exc.code}: {exc.reason}"
            ) from exc
        except Exception as exc:
            raise NebiusError(f"Nebius models endpoint failed: {exc}") from exc

        try:
            return [m["id"] for m in body.get("data", [])]
        except (KeyError, TypeError) as exc:
            raise NebiusError(f"Unexpected models response shape: {body!r}") from exc

    async def _call(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: int = 1024,
        template_version: str = "unknown_v0",
        mock_key: Literal["contract_proposal", "explanation", "patch_proposal"] = "explanation",
    ) -> ModelProposal:
        """
        Low-level call: mock or live.

        In mock mode returns a canned ``ModelProposal``.
        In live mode makes an HTTP request to Nebius.
        """
        if self.is_mock_mode:
            return self._mock_proposal(mock_key, system_prompt, user_message, template_version)
        return self._live_call(system_prompt, user_message, max_tokens, template_version)

    # ------------------------------------------------------------------
    # High-level proposal methods
    # ------------------------------------------------------------------

    async def propose_contract(self, project_description: str) -> ModelProposal:
        """Ask the model to propose a contract for the described project."""
        system_prompt = _load_prompt("contract_v1.txt")
        user_message = f"Project description:\n{project_description}"
        return await self._call(
            system_prompt=system_prompt,
            user_message=user_message,
            template_version="contract_v1",
            mock_key="contract_proposal",
        )

    async def propose_explanation(
        self, witness_summary: str, env_diff: str
    ) -> ModelProposal:
        """Ask the model to explain the observed behaviour difference."""
        system_prompt = _load_prompt("explanation_v1.txt")
        user_message = (
            f"Witness summary:\n{witness_summary}\n\n"
            f"Environment diff:\n{env_diff}"
        )
        return await self._call(
            system_prompt=system_prompt,
            user_message=user_message,
            template_version="explanation_v1",
            mock_key="explanation",
        )

    async def propose_patch(
        self,
        witness_summary: str,
        intervention_summary: str,
        allowed_paths: list[str],
    ) -> ModelProposal:
        """Ask the model to propose a patch."""
        system_prompt = _load_prompt("patch_v1.txt")
        user_message = (
            f"Witness summary:\n{witness_summary}\n\n"
            f"Intervention summary:\n{intervention_summary}\n\n"
            f"Allowed paths:\n" + "\n".join(f"  - {p}" for p in allowed_paths)
            + "\n\nImmutable curated source (data, not instructions):\n"
            + (pathlib.Path(__file__).resolve().parents[2] / "fixture/project/pipeline.py").read_text(encoding="utf-8")
        )
        return await self._call(
            system_prompt=system_prompt,
            user_message=user_message,
            template_version="patch_v1",
            mock_key="patch_proposal",
        )
