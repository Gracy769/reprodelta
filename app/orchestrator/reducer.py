"""
Witness reducer — simplifies a conclusion-changing input.

For the 8-element signal, reduction means:
1. Try to replace individual elements with "neutral" values (e.g., 1.0)
   while preserving the conclusion change.
2. Accept a reduction if: input is still valid, and both envs still show the
   conclusion change.

Always recheck validity and conclusion after every accepted reduction.
"""
from __future__ import annotations

import hashlib
import json
import uuid

from pydantic import BaseModel, ConfigDict

from app.domain.contract import ContractBody
from app.domain.validator import validate_input
from app.orchestrator.executor import PairedRunRequest, run_pair
from app.providers.runner import RunnerAdapter

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class ReductionStep(BaseModel):
    model_config = ConfigDict(frozen=True)

    step_id: int
    original_input: dict
    reduced_input: dict
    reduction_type: str
    accepted: bool
    reason: str


class ReductionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    final_witness: dict
    final_sha256: str
    steps: list[ReductionStep]
    size_before: int
    size_after: int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash_input(input_json: dict) -> str:
    return hashlib.sha256(
        json.dumps(input_json, sort_keys=True).encode()
    ).hexdigest()


def _conclusion_changes(
    input_json: dict,
    contract: ContractBody,
    runner: RunnerAdapter,
) -> bool:
    """Return True iff the input is valid AND old/new conclusions differ."""
    validation = validate_input(contract, input_json)
    if not validation.valid:
        return False
    sha = _hash_input(input_json)
    req = PairedRunRequest(
        investigation_id=uuid.uuid4(),
        candidate=input_json,
        candidate_sha256=sha,
        contract=contract,
    )
    result = run_pair(req, runner)
    return result.comparison.get("conclusion_changed") is True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def reduce_witness(
    witness_input: dict,
    contract: ContractBody,
    runner: RunnerAdapter,
    max_steps: int = 20,
) -> ReductionResult:
    """
    Attempt to simplify *witness_input* by replacing individual signal elements
    with the neutral value 1.0.  Each replacement is accepted only if the
    resulting input is still valid AND still produces a conclusion change.

    For the fixture witness [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0+1ULP]
    this will quickly confirm that all elements except index 7 are already at
    1.0 and that replacing index 7 with 1.0 removes the witness property —
    so the original is already minimal.

    Returns a ReductionResult describing the final (minimal) form and all
    attempted steps.
    """
    NEUTRAL = 1.0

    # Work with the signal list; fall back gracefully if "signal" key missing.
    current = dict(witness_input)
    signal_key = next(iter(current))  # assumes single-field contract
    current_signal: list = list(current[signal_key])

    size_before = len(current_signal)
    steps: list[ReductionStep] = []
    step_id = 0

    for idx in range(len(current_signal)):
        if step_id >= max_steps:
            break

        original_val = current_signal[idx]
        if original_val == NEUTRAL:
            # Already neutral — record as a trivially accepted no-op so tests
            # can assert that every element was inspected.
            steps.append(
                ReductionStep(
                    step_id=step_id,
                    original_input={signal_key: list(current_signal)},
                    reduced_input={signal_key: list(current_signal)},
                    reduction_type="neutral_replace",
                    accepted=True,
                    reason="element already at neutral value",
                )
            )
            step_id += 1
            continue

        # Try replacing with neutral
        trial_signal = list(current_signal)
        trial_signal[idx] = NEUTRAL
        trial_input = {signal_key: trial_signal}

        original_input_snapshot = {signal_key: list(current_signal)}

        changes = _conclusion_changes(trial_input, contract, runner)

        if changes:
            # Accept: this element wasn't contributing to the witness property
            current_signal = trial_signal
            steps.append(
                ReductionStep(
                    step_id=step_id,
                    original_input=original_input_snapshot,
                    reduced_input={signal_key: list(current_signal)},
                    reduction_type="neutral_replace",
                    accepted=True,
                    reason=f"element[{idx}] replaced with {NEUTRAL}; witness property preserved",
                )
            )
        else:
            # Reject: removing this element breaks the witness property
            steps.append(
                ReductionStep(
                    step_id=step_id,
                    original_input=original_input_snapshot,
                    reduced_input=trial_input,
                    reduction_type="neutral_replace",
                    accepted=False,
                    reason=f"element[{idx}] cannot be neutralised; witness property lost",
                )
            )
        step_id += 1

    final_witness = {signal_key: list(current_signal)}
    final_sha256 = _hash_input(final_witness)

    return ReductionResult(
        final_witness=final_witness,
        final_sha256=final_sha256,
        steps=steps,
        size_before=size_before,
        size_after=len(current_signal),
    )
