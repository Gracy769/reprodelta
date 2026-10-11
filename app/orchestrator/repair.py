"""
Patch proposal and independent verification.

The model proposes a patch; the independent verifier runs it against
frozen holdout inputs. The repair proposer cannot read or modify holdouts.

For the fixture:
- Good patch: replace `signal / 2.0` with `signal / np.float32(2.0)` 
  (this is actually the same as the intervention — fixing the dtype consistency)
- Overfitted patch: add a special case `if signal[7] >= 1.0: return 0.4999...`
  (handles the specific witness but breaks holdout cases)
- Holdouts: 5 inputs that should have consistent behavior in both envs
"""
from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.domain.contract import ContractBody
from app.providers.runner import RunManifest, RunnerAdapter, make_run_id

# ---------------------------------------------------------------------------
# Frozen holdout set (must be sealed BEFORE any patch generation)
# ---------------------------------------------------------------------------

FIXTURE_HOLDOUTS: list[dict] = [
    # These are valid inputs where old_conclusion should == new_conclusion
    # (they are NOT near the ULP boundary, so float32 precision doesn't matter)
    {"signal": [0.5, 0.5, 0.5, 0.5, 1.5, 1.5, 1.5, 1.5]},  # clearly above
    {"signal": [1.5, 1.5, 1.5, 1.5, 0.5, 0.5, 0.5, 0.5]},  # clearly below
    {"signal": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]},  # equal → float32 tie
    {"signal": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]},  # mixed
    {"signal": [2.0, 2.0, 2.0, 2.0, 3.0, 3.0, 3.0, 3.0]},  # clearly above
    # Regression-detector: clearly above_threshold in both envs, but overfitted patch
    # returns below_threshold because signal[7] is slightly above 1.0 (triggers the hack).
    # Good patch: consistent above_threshold. Overfitted patch: regression → fail.
    {"signal": [0.5, 0.5, 0.5, 0.5, 1.5, 1.5, 1.5, 1.000005]},  # above; signal[7] trips overfitted hack
]


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class PatchKind(StrEnum):
    GOOD = "good"
    OVERFITTED = "overfitted"


class PatchDiff(BaseModel):
    """Describes a proposed patch."""

    model_config = ConfigDict(frozen=True)

    kind: PatchKind
    diff_text: str
    description: str
    rationale: str


class HoldoutResult(BaseModel):
    """Result of running one holdout input through both environments."""

    model_config = ConfigDict(frozen=True)

    input_json: dict
    input_sha256: str
    old_conclusion: str | None
    new_conclusion: str | None
    conclusion_consistent: bool
    patched_old_conclusion: str | None
    patched_new_conclusion: str | None
    check_status: Literal["pass", "fail", "not_run"]


class VerificationResult(BaseModel):
    """Aggregate result of verifying a patch against all holdouts."""

    model_config = ConfigDict(frozen=True)

    patch_kind: PatchKind
    holdout_results: list[HoldoutResult]
    all_passed: bool
    num_passed: int
    num_failed: int
    verdict: Literal[
        "repair_passes_stated_checks",
        "repair_fails_stated_checks",
        "inconclusive",
    ]
    details: str


# ---------------------------------------------------------------------------
# Predefined patches
# ---------------------------------------------------------------------------

GOOD_PATCH = PatchDiff(
    kind=PatchKind.GOOD,
    diff_text=(
        "--- a/fixture/project/pipeline.py\n"
        "+++ b/fixture/project/pipeline.py\n"
        "@@ -28 +28 @@\n"
        "-    normalized = signal / 2.0\n"
        "+    normalized = signal / np.float32(2.0)"
    ),
    description="Use np.float32 divisor to ensure consistent intermediate dtype",
    rationale="Prevents NEP 50 from changing precision between NumPy 1.26 and 2.0",
)

OVERFITTED_PATCH = PatchDiff(
    kind=PatchKind.OVERFITTED,
    diff_text=(
        "--- a/fixture/project/pipeline.py\n"
        "+++ b/fixture/project/pipeline.py\n"
        "@@ -25 +25 @@\n"
        "+    if len(signal) == 8 and abs(float(signal[7]) - 1.0) < 1e-5 and float(signal[7]) > 1.0:\n"
        "+        return 0.4999999  # hard-coded witness workaround\n"
    ),
    description="Special-case the discovered witness input only",
    rationale=(
        "Overfitted: only fixes the specific witness, not the general precision issue"
    ),
)


# ---------------------------------------------------------------------------
# Mode map — maps PatchKind → _mode value for run.py
# ---------------------------------------------------------------------------

_PATCH_MODE: dict[PatchKind, str] = {
    PatchKind.GOOD: "explicit_float32",
    PatchKind.OVERFITTED: "overfitted",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash_input(input_json: dict) -> str:
    return hashlib.sha256(
        json.dumps(input_json, sort_keys=True).encode()
    ).hexdigest()


def _run_one(
    input_json: dict,
    kind: Literal["old", "new"],
    timeout: int,
    runner: RunnerAdapter,
) -> tuple[str, str | None]:
    """Submit a single run and return (status, conclusion)."""
    sha256 = _hash_input(input_json)
    manifest = RunManifest(
        run_id=make_run_id(),
        kind=kind,
        candidate_sha256=sha256,
        entry_point="run.py",
        input_json=input_json,
        timeout_seconds=timeout,
    )
    result = runner.submit(manifest)
    outputs = result.outputs
    inner = outputs.get("result", outputs)
    conclusion = inner.get("conclusion") if isinstance(inner, dict) else None
    return result.status, conclusion


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def verify_patch(
    patch: PatchDiff,
    holdouts: list[dict],
    contract: ContractBody,
    runner: RunnerAdapter,
) -> VerificationResult:
    """
    Verify a patch by running each holdout through both environments.

    For each holdout:
    1. Run original pair (no patch) to establish the baseline.
    2. Run patched pair (with ``_mode`` injected matching the patch kind).
    3. Check: after patch, do old/new conclusions still agree?
       (For inputs that were already consistent: they must remain consistent.)

    A holdout "passes" when conclusions are consistent AFTER the patch is applied.

    Parameters
    ----------
    patch:
        The patch to verify.
    holdouts:
        Frozen list of holdout inputs (must not be modified by the patch).
    contract:
        Contract governing input validity and timeouts.
    runner:
        Runner adapter for dispatching runs.

    Returns
    -------
    VerificationResult
        Aggregate pass/fail summary with per-holdout detail.
    """
    mode = _PATCH_MODE[patch.kind]
    timeout = contract.resource_limits.max_wall_seconds
    holdout_results: list[HoldoutResult] = []

    for holdout_input in holdouts:
        sha256 = _hash_input(holdout_input)

        # Baseline: run without patch
        old_status, old_conclusion = _run_one(holdout_input, "old", timeout, runner)
        new_status, new_conclusion = _run_one(holdout_input, "new", timeout, runner)
        baseline_consistent = (
            old_status == "ok"
            and new_status == "ok"
            and old_conclusion == new_conclusion
        )

        # Patched run: inject _mode
        patched_input = {**holdout_input, "_mode": mode}
        p_old_status, p_old_conclusion = _run_one(patched_input, "old", timeout, runner)
        p_new_status, p_new_conclusion = _run_one(patched_input, "new", timeout, runner)

        if p_old_status != "ok" or p_new_status != "ok":
            check_status: Literal["pass", "fail", "not_run"] = "not_run"
        elif p_old_conclusion != p_new_conclusion:
            # After patch, old and new conclusions still differ → fail
            check_status = "fail"
        elif baseline_consistent and p_old_conclusion != old_conclusion:
            # Patch changed a previously-consistent holdout's conclusion → regression
            check_status = "fail"
        else:
            check_status = "pass"

        holdout_results.append(
            HoldoutResult(
                input_json=holdout_input,
                input_sha256=sha256,
                old_conclusion=old_conclusion,
                new_conclusion=new_conclusion,
                conclusion_consistent=baseline_consistent,
                patched_old_conclusion=p_old_conclusion,
                patched_new_conclusion=p_new_conclusion,
                check_status=check_status,
            )
        )

    num_passed = sum(1 for r in holdout_results if r.check_status == "pass")
    num_failed = sum(1 for r in holdout_results if r.check_status == "fail")
    num_not_run = sum(1 for r in holdout_results if r.check_status == "not_run")
    all_passed = num_failed == 0 and num_not_run == 0 and num_passed == len(holdouts)

    if num_not_run > 0 and num_passed == 0:
        verdict: Literal[
            "repair_passes_stated_checks",
            "repair_fails_stated_checks",
            "inconclusive",
        ] = "inconclusive"
    elif all_passed:
        verdict = "repair_passes_stated_checks"
    else:
        verdict = "repair_fails_stated_checks"

    details_parts = [f"{num_passed}/{len(holdouts)} holdouts passed"]
    if num_failed > 0:
        failed_indices = [
            i for i, r in enumerate(holdout_results) if r.check_status == "fail"
        ]
        details_parts.append(f"failed holdouts: {failed_indices}")
    if num_not_run > 0:
        details_parts.append(f"{num_not_run} holdouts could not run")

    return VerificationResult(
        patch_kind=patch.kind,
        holdout_results=holdout_results,
        all_passed=all_passed,
        num_passed=num_passed,
        num_failed=num_failed,
        verdict=verdict,
        details="; ".join(details_parts),
    )
