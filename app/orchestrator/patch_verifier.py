"""Validate the scope of an untrusted patch proposal.

This module does not apply proposed source code. The curated repair fixtures in
``repair.py`` are separate demonstrations and cannot establish that an arbitrary
model or user supplied diff passes verification.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.contract import ContractBody
from app.orchestrator.repair import HoldoutResult
from app.providers.runner import RunnerAdapter

_ALLOWED_PATH = "fixture/project/pipeline.py"


class ProposedPatch(BaseModel):
    model_config = ConfigDict(frozen=True)

    diff_text: str = Field(max_length=100_000)
    description: str = ""
    rationale: str = ""


class PatchParseResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    valid: bool
    reason: str | None
    allowed_paths: list[str]
    rejected_paths: list[str]


class PatchCheckResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    check_type: str
    status: Literal["pass", "fail", "not_run", "inconclusive"]
    details: str


class ConstrainedPatchVerificationResult(BaseModel):
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


def validate_patch_proposal(diff_text: str) -> PatchParseResult:
    """Check that one unified diff names only the curated source file.

    This is a scope check, not a guarantee that the hunks apply cleanly.
    """
    lines = diff_text.splitlines()
    unsupported = [
        line for line in lines
        if line.startswith(("rename from ", "rename to ", "copy from ", "copy to ", "GIT binary patch", "Binary files "))
    ]
    old_paths = [line[4:].strip() for line in lines if line.startswith("--- ")]
    new_paths = [line[4:].strip() for line in lines if line.startswith("+++ ")]
    git_headers = [line for line in lines if line.startswith("diff --git ")]
    paths = old_paths + new_paths
    rejected = [path for path in paths if path not in (
        f"a/{_ALLOWED_PATH}", f"b/{_ALLOWED_PATH}"
    )]
    allowed = [_ALLOWED_PATH] if paths and not rejected else []
    headers_valid = (
        len(old_paths) == len(new_paths) == 1
        and old_paths[0] == f"a/{_ALLOWED_PATH}"
        and new_paths[0] == f"b/{_ALLOWED_PATH}"
        and any(line.startswith("@@ ") for line in lines)
        and any(line.startswith("+") and not line.startswith("+++") for line in lines)
    )
    if unsupported:
        reason = "unsupported_diff_metadata"
    elif git_headers and git_headers != [f"diff --git a/{_ALLOWED_PATH} b/{_ALLOWED_PATH}"]:
        reason = "rejected_paths: unsupported git diff header"
    elif rejected:
        reason = f"rejected_paths: {', '.join(rejected)}"
    elif not headers_valid:
        reason = "not_a_diff: expected one allowlisted file with a hunk and added lines"
    else:
        reason = None
    return PatchParseResult(
        valid=reason is None,
        reason=reason,
        allowed_paths=allowed,
        rejected_paths=rejected,
    )


def apply_and_verify_patch(
    proposal: ProposedPatch,
    holdouts: list[dict] | None = None,
    witness_input: dict | None = None,
    contract: ContractBody | None = None,
    runner: RunnerAdapter | None = None,
) -> ConstrainedPatchVerificationResult:
    """Return a bounded assessment without claiming an unexecuted diff passed.

    The existing runner protocol accepts inputs, not patched project snapshots.
    Until that protocol is extended, every in-scope proposal remains inconclusive.
    """
    parsed = validate_patch_proposal(proposal.diff_text)
    path_check = PatchCheckResult(
        check_type="path_allowlist",
        status="pass" if parsed.valid else "fail",
        details=parsed.reason or "Only the curated pipeline path was named",
    )
    if not parsed.valid:
        return ConstrainedPatchVerificationResult(
            proposal=proposal,
            parse_result=parsed,
            holdout_results=[],
            patch_checks=[path_check],
            verdict="invalid_patch",
            limitations=["No patch was applied or executed."],
        )

    return ConstrainedPatchVerificationResult(
        proposal=proposal,
        parse_result=parsed,
        holdout_results=[],
        patch_checks=[
            path_check,
            PatchCheckResult(
                check_type="patch_execution",
                status="not_run",
                details="The runner cannot execute a proposed source diff yet.",
            ),
        ],
        verdict="inconclusive",
        limitations=[
            "The diff was not applied to project source in either environment.",
            "The predefined fixture repair checks do not verify this proposal.",
        ],
    )
