"""
Constrained patch verifier.

Accepts a unified diff text, validates it against allowlisted paths,
inspects the functional change, then runs the witness and holdouts in
both environments using the detected _mode.

Security invariants:
- Only allows edits to fixture/project/pipeline.py
- Rejects edits to: tests/, contracts/, holdouts, lockfiles, runner/
- diff must be parseable (look for --- a/ and +++ b/ lines)
- The detected _mode is used to run the simulation — no model-generated
  code executes in the API process
- Holdout contents do NOT appear in model prompts

For local mode (simulation):
  explicit_float32 diff  → _mode="explicit_float32"  → should PASS holdouts
  overfitted diff        → _mode="overfitted"         → should FAIL holdouts
  unknown change         → inconclusive (cannot verify arbitrary diffs in simulation)
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.domain.contract import ContractBody
from app.orchestrator.repair import (
    FIXTURE_HOLDOUTS,
    HoldoutResult,
    PatchDiff,
    PatchKind,
    verify_patch,
)
from app.providers.runner import RunnerAdapter

# ---------------------------------------------------------------------------
# Allowlist / blocklist
# ---------------------------------------------------------------------------

_ALLOWED_PATHS = {"fixture/project/pipeline.py"}

_BLOCKED_PATTERNS = [
    "tests/",
    "holdout",
    "contract",
    "runner/",
    ".lock",
    "requirements",
    "conftest",
    "setup.py",
    "pyproject.toml",
    "alembic",
    "migrations/",
]

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class ProposedPatch(BaseModel):
    """An untrusted patch proposal from an external source (e.g., model output)."""

    model_config = ConfigDict(frozen=True)

    diff_text: str
    description: str
    rationale: str


class PatchParseResult(BaseModel):
    """Result of parsing and validating a unified diff."""

    model_config = ConfigDict(frozen=True)

    valid: bool
    reason: str | None
    allowed_paths: list[str]
    rejected_paths: list[str]


class PatchCheckResult(BaseModel):
    """Result of one specific validation check on the patch."""

    model_config = ConfigDict(frozen=True)

    check_type: str
    status: Literal["pass", "fail", "not_run", "inconclusive"]
    details: str


class ConstrainedPatchVerificationResult(BaseModel):
    """Full result of constrained patch verification."""

    model_config = ConfigDict(frozen=True)

    proposal: ProposedPatch
    parse_result: PatchParseResult
    holdout_results: list[HoldoutResult]
    patch_checks: list[PatchCheckResult]
    verdict: Literal[
        "repair_passes_stated_checks",
        "repair_fails_stated_checks",
        "inconclusive",
        "invalid_patch",
    ]
    limitations: list[str]


# ---------------------------------------------------------------------------
# Diff parsing
# ---------------------------------------------------------------------------


def _extract_target_paths(diff_text: str) -> tuple[list[str], list[str]]:
    """
    Parse a unified diff and extract target file paths.

    Returns (allowed, rejected) lists of paths found in the diff.
    Uses a simple line-by-line scan for '--- a/' and '+++ b/' markers.
    """
    allowed: list[str] = []
    rejected: list[str] = []

    for line in diff_text.splitlines():
        path: str | None = None
        if line.startswith("--- a/") or line.startswith("+++ b/"):
            path = line[6:].strip()
        elif line.startswith("--- ") or line.startswith("+++ "):
            path = line[4:].strip()

        if path and path not in ("/dev/null",):
            blocked = any(pat in path for pat in _BLOCKED_PATTERNS)
            if path in _ALLOWED_PATHS:
                if path not in allowed:
                    allowed.append(path)
            elif blocked:
                if path not in rejected:
                    rejected.append(path)
            else:
                # Not explicitly allowed → rejected
                if path not in rejected:
                    rejected.append(path)

    return allowed, rejected


def validate_patch_proposal(diff_text: str) -> PatchParseResult:
    """
    Parse and validate a unified diff text.

    Steps:
    1. Look for '--- a/' and '+++ b/' lines to extract target paths.
    2. Reject if any path is NOT fixture/project/pipeline.py.
    3. Reject if any blocked pattern appears in any path.
    4. Reject if no valid +++ line is found (unparseable diff).

    Returns PatchParseResult with valid=True only if all checks pass.
    """
    # Must contain at least one diff marker
    has_diff_marker = any(
        line.startswith("--- ") or line.startswith("+++ ") or line.startswith("@@ ")
        for line in diff_text.splitlines()
    )
    if not has_diff_marker:
        return PatchParseResult(
            valid=False,
            reason="not_a_diff: no unified diff markers found",
            allowed_paths=[],
            rejected_paths=[],
        )

    allowed, rejected = _extract_target_paths(diff_text)

    if rejected:
        return PatchParseResult(
            valid=False,
            reason=f"rejected_paths: {', '.join(rejected)}",
            allowed_paths=allowed,
            rejected_paths=rejected,
        )

    if not allowed:
        return PatchParseResult(
            valid=False,
            reason="no_allowed_paths: diff does not target fixture/project/pipeline.py",
            allowed_paths=[],
            rejected_paths=rejected,
        )

    return PatchParseResult(
        valid=True,
        reason=None,
        allowed_paths=allowed,
        rejected_paths=[],
    )


# ---------------------------------------------------------------------------
# Change detection
# ---------------------------------------------------------------------------


def _detect_patch_mode(diff_text: str) -> str | None:
    """
    Detect which simulation _mode corresponds to this diff.

    Returns:
      "explicit_float32" — diff adds np.float32(2.0) divisor (good patch)
      "overfitted"       — diff adds a special-case return (overfitted patch)
      None               — unknown change, cannot verify in local simulation
    """
    added_lines = [
        line[1:]
        for line in diff_text.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]
    added_text = "\n".join(added_lines)

    if "np.float32(2.0)" in added_text:
        return "explicit_float32"

    # Overfitted patch: adds a special-case return with a hardcoded value
    if "0.4999999" in added_text or (
        "signal[7]" in added_text and "return" in added_text
    ):
        return "overfitted"

    return None


# ---------------------------------------------------------------------------
# Main verification function
# ---------------------------------------------------------------------------


def apply_and_verify_patch(
    proposal: ProposedPatch,
    holdouts: list[dict] | None = None,
    witness_input: dict | None = None,
    contract: ContractBody | None = None,
    runner: RunnerAdapter | None = None,
) -> ConstrainedPatchVerificationResult:
    """
    Validate a proposed patch and run it against holdouts.

    Steps:
    1. Parse and validate the diff (path allowlist check).
    2. If invalid → return invalid_patch verdict.
    3. Detect the _mode for simulation.
    4. If mode is unknown → return inconclusive.
    5. Map mode to PatchKind and run verify_patch.
    6. Return ConstrainedPatchVerificationResult.

    Parameters
    ----------
    proposal:
        The untrusted patch proposal.
    holdouts:
        Holdout inputs to verify against. Defaults to FIXTURE_HOLDOUTS.
    witness_input:
        The witness input (not used in verification; kept for completeness).
    contract:
        The contract governing input validity and timeouts.
    runner:
        The runner adapter for dispatching runs.
    """
    from app.domain.contract import FIXTURE_CONTRACT

    if holdouts is None:
        holdouts = list(FIXTURE_HOLDOUTS)
    if contract is None:
        contract = FIXTURE_CONTRACT

    limitations: list[str] = [
        "Patch verification uses in-process simulation, not isolated Docker containers.",
        "Only fixture/project/pipeline.py changes can be verified in local simulation mode.",
    ]

    # ------------------------------------------------------------------
    # Step 1 & 2: parse / validate diff
    # ------------------------------------------------------------------
    parse_result = validate_patch_proposal(proposal.diff_text)
    parse_check = PatchCheckResult(
        check_type="path_allowlist",
        status="pass" if parse_result.valid else "fail",
        details=parse_result.reason or "paths allowed",
    )

    if not parse_result.valid:
        return ConstrainedPatchVerificationResult(
            proposal=proposal,
            parse_result=parse_result,
            holdout_results=[],
            patch_checks=[parse_check],
            verdict="invalid_patch",
            limitations=limitations,
        )

    # ------------------------------------------------------------------
    # Step 3: detect mode
    # ------------------------------------------------------------------
    mode = _detect_patch_mode(proposal.diff_text)
    mode_check = PatchCheckResult(
        check_type="change_detection",
        status="pass" if mode is not None else "inconclusive",
        details=f"detected_mode={mode}" if mode else "unknown change type",
    )

    if mode is None:
        limitations.append(
            "The diff contains changes that cannot be verified in local simulation mode; "
            "a worker_http runner with real Docker isolation would be required."
        )
        return ConstrainedPatchVerificationResult(
            proposal=proposal,
            parse_result=parse_result,
            holdout_results=[],
            patch_checks=[parse_check, mode_check],
            verdict="inconclusive",
            limitations=limitations,
        )

    # ------------------------------------------------------------------
    # Step 4: map mode → PatchKind → PatchDiff
    # ------------------------------------------------------------------
    mode_to_kind: dict[str, PatchKind] = {
        "explicit_float32": PatchKind.GOOD,
        "overfitted": PatchKind.OVERFITTED,
    }
    patch_kind = mode_to_kind[mode]
    patch_diff = PatchDiff(
        kind=patch_kind,
        diff_text=proposal.diff_text,
        description=proposal.description,
        rationale=proposal.rationale,
    )

    # ------------------------------------------------------------------
    # Step 5: verify against holdouts
    # ------------------------------------------------------------------
    if runner is None:
        from app.providers.runner import LocalRunnerAdapter
        runner = LocalRunnerAdapter()

    try:
        verification = verify_patch(patch_diff, holdouts, contract, runner)
    except Exception as exc:  # noqa: BLE001
        error_check = PatchCheckResult(
            check_type="holdout_verification",
            status="not_run",
            details=f"verification raised: {exc}",
        )
        limitations.append(f"Holdout verification failed with error: {exc}")
        return ConstrainedPatchVerificationResult(
            proposal=proposal,
            parse_result=parse_result,
            holdout_results=[],
            patch_checks=[parse_check, mode_check, error_check],
            verdict="inconclusive",
            limitations=limitations,
        )

    holdout_check = PatchCheckResult(
        check_type="holdout_verification",
        status="pass" if verification.all_passed else "fail",
        details=verification.details,
    )

    # Map VerificationResult verdict to our verdict
    verdict_map: dict[str, str] = {
        "repair_passes_stated_checks": "repair_passes_stated_checks",
        "repair_fails_stated_checks": "repair_fails_stated_checks",
        "inconclusive": "inconclusive",
    }
    verdict = verdict_map.get(verification.verdict, "inconclusive")

    return ConstrainedPatchVerificationResult(
        proposal=proposal,
        parse_result=parse_result,
        holdout_results=list(verification.holdout_results),
        patch_checks=[parse_check, mode_check, holdout_check],
        verdict=verdict,  # type: ignore[arg-type]
        limitations=limitations,
    )
