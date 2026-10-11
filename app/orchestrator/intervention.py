"""
Intervention controller.

An intervention changes one variable (e.g., the dtype of an intermediate
computation) and re-runs both environments. It records whether the
conclusion difference disappeared (supporting the proposed mechanism) or
persisted (contradicting it).

For the fixture: the proposed intervention is to explicitly use float32
scalar division (sample / np.float32(2.0)) instead of (sample / 2.0).
This tests whether the NEP 50 precision difference is the mechanism.

The intervention patches only the fixture's pipeline code IN MEMORY
(not on disk), runs it, and checks:
- If old_conclusion == new_conclusion after intervention: mechanism SUPPORTED
- If old_conclusion != new_conclusion after intervention: mechanism CONTRADICTED
"""
from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.domain.contract import ContractBody
from app.domain.evidence import RunEvidence
from app.providers.nebius import ModelProposal
from app.providers.runner import RunManifest, RunnerAdapter, make_run_id

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class InterventionKind(StrEnum):
    EXPLICIT_FLOAT32_DIVISOR = "explicit_float32_divisor"


class InterventionManifest(BaseModel):
    """Describes a single controlled intervention to test a proposed mechanism."""

    model_config = ConfigDict(frozen=True)

    kind: InterventionKind
    description: str
    predicted_effect: str
    change_description: str


class InterventionRunResult(BaseModel):
    """Result of running both environments with the intervention applied."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    kind: InterventionKind
    old_run: RunEvidence
    new_run: RunEvidence
    conclusion_changed: bool
    prediction_supported: bool
    assessment: Literal["supported", "contradicted", "inconclusive"]


# ---------------------------------------------------------------------------
# Predefined fixture intervention
# ---------------------------------------------------------------------------

FIXTURE_INTERVENTION = InterventionManifest(
    kind=InterventionKind.EXPLICIT_FLOAT32_DIVISOR,
    description=(
        "Replace `signal / 2.0` with `signal / np.float32(2.0)` "
        "to force float32 intermediate"
    ),
    predicted_effect=(
        "Conclusion change disappears because both environments "
        "now use float32 arithmetic"
    ),
    change_description="normalize divisor dtype from Python float to np.float32",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_run_evidence(result_outputs: dict, run_id: str, environment: str) -> RunEvidence:
    """Build a RunEvidence from a raw runner output dict."""
    inner = result_outputs.get("result", result_outputs)
    status = result_outputs.get("status", "error")
    if isinstance(inner, dict):
        return RunEvidence(
            run_id=run_id,
            environment=environment,
            numpy_version=inner.get("numpy_version"),
            conclusion=inner.get("conclusion"),
            ratio=float(inner["ratio"]) if "ratio" in inner else None,
            intermediate_dtype=inner.get("intermediate_dtype"),
            status=status,
        )
    return RunEvidence(
        run_id=run_id,
        environment=environment,
        numpy_version=None,
        conclusion=None,
        ratio=None,
        intermediate_dtype=None,
        status=status,
    )


def _hash_input(input_json: dict) -> str:
    return hashlib.sha256(
        json.dumps(input_json, sort_keys=True).encode()
    ).hexdigest()


# ---------------------------------------------------------------------------
# Intervention runner
# ---------------------------------------------------------------------------


def run_intervention(
    witness_input: dict,
    intervention: InterventionManifest,
    contract: ContractBody,
    runner: RunnerAdapter,
    proposal: ModelProposal,  # noqa: ARG001 — stored for provenance, used by caller
) -> InterventionRunResult:
    """
    Run both environments with the intervention applied and assess the result.

    The intervention is communicated to the fixture via the ``_mode`` field
    injected into ``input_json``.  The fixture's ``run.py`` checks this field
    and applies the corresponding arithmetic variant.

    Parameters
    ----------
    witness_input:
        The input dict that caused the conclusion to change in the original run.
    intervention:
        Description of the intervention to apply.
    contract:
        The contract governing valid inputs.
    runner:
        The runner adapter to dispatch runs through.
    proposal:
        The model proposal that motivated this intervention (kept for caller
        provenance; not used to determine the assessment).

    Returns
    -------
    InterventionRunResult
        Contains the evidence from both patched runs and the assessment.
    """
    # Map intervention kind → _mode value passed to run.py
    _MODE_MAP = {
        InterventionKind.EXPLICIT_FLOAT32_DIVISOR: "explicit_float32",
    }
    mode = _MODE_MAP[intervention.kind]

    # Inject the mode key into the input dict (the fixture strips it before
    # using "signal", so it does not affect contract validity)
    patched_input = {**witness_input, "_mode": mode}
    sha256 = _hash_input(patched_input)
    timeout = contract.resource_limits.max_wall_seconds

    old_manifest = RunManifest(
        run_id=make_run_id(),
        kind="old",
        candidate_sha256=sha256,
        entry_point="run.py",
        input_json=patched_input,
        timeout_seconds=timeout,
    )
    new_manifest = RunManifest(
        run_id=make_run_id(),
        kind="new",
        candidate_sha256=sha256,
        entry_point="run.py",
        input_json=patched_input,
        timeout_seconds=timeout,
    )

    old_result = runner.submit(old_manifest)
    new_result = runner.submit(new_manifest)

    old_ev = _extract_run_evidence(old_result.outputs, old_result.run_id, "old")
    new_ev = _extract_run_evidence(new_result.outputs, new_result.run_id, "new")

    # The conclusion_changed flag is set based on the PATCHED run outputs.
    # If both runs now agree → the intervention removed the difference.
    conclusion_changed = (
        old_ev.conclusion is not None
        and new_ev.conclusion is not None
        and old_ev.conclusion != new_ev.conclusion
    )

    # The intervention PREDICTED that conclusion_changed disappears.
    # So prediction_supported = True when conclusion_changed is False.
    prediction_supported = not conclusion_changed

    if old_result.status != "ok" or new_result.status != "ok":
        assessment: Literal["supported", "contradicted", "inconclusive"] = "inconclusive"
    elif prediction_supported:
        assessment = "supported"
    else:
        assessment = "contradicted"

    return InterventionRunResult(
        kind=intervention.kind,
        old_run=old_ev,
        new_run=new_ev,
        conclusion_changed=conclusion_changed,
        prediction_supported=prediction_supported,
        assessment=assessment,
    )
