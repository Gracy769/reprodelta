"""Apply a constrained model diff and verify worker evidence against frozen cases."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.comparison import evaluate_output
from app.domain.contract import ContractBody, hash_contract
from app.domain.diagnostics import preserve_measurement
from app.domain.repair_oracle import RepairOracle
from app.domain.validator import validate_input
from app.orchestrator.repair import HoldoutResult
from app.providers.runner import RunManifest, RunnerAdapter, make_run_id
from runner.patching import apply_diff, project_digest, project_sources

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
    witness_result: HoldoutResult | None = None
    execution_runs: list[dict] = []
    base_project_sha256: str | None = None
    patched_project_sha256: str | None = None
    contract_sha256: str | None = None
    holdouts_sha256: str | None = None
    measurement_checks: list[dict] = []
    scientific_oracle: dict = {"status": "not_supplied", "scope": "Compatibility checks only"}


def validate_patch_proposal(diff_text: str) -> PatchParseResult:
    """Check that one unified diff names only the curated source file.

    This is a scope check, not a guarantee that the hunks apply cleanly.
    """
    lines = diff_text.splitlines()
    unsupported = [
        line
        for line in lines
        if line.startswith(
            (
                "rename from ",
                "rename to ",
                "copy from ",
                "copy to ",
                "GIT binary patch",
                "Binary files ",
            )
        )
    ]
    old_paths = [line[4:].strip() for line in lines if line.startswith("--- ")]
    new_paths = [line[4:].strip() for line in lines if line.startswith("+++ ")]
    git_headers = [line for line in lines if line.startswith("diff --git ")]
    paths = old_paths + new_paths
    rejected = [path for path in paths if path not in (f"a/{_ALLOWED_PATH}", f"b/{_ALLOWED_PATH}")]
    allowed = [_ALLOWED_PATH] if paths and not rejected else []
    headers_valid = (
        len(old_paths) == len(new_paths) == 1
        and old_paths[0] == f"a/{_ALLOWED_PATH}"
        and new_paths[0] == f"b/{_ALLOWED_PATH}"
        and any(line.startswith("@@ ") for line in lines)
        and any(
            line.startswith(("+", "-")) and not line.startswith(("+++", "---")) for line in lines
        )
    )
    if unsupported:
        reason = "unsupported_diff_metadata"
    elif git_headers and git_headers != [f"diff --git a/{_ALLOWED_PATH} b/{_ALLOWED_PATH}"]:
        reason = "rejected_paths: unsupported git diff header"
    elif rejected:
        reason = f"rejected_paths: {', '.join(rejected)}"
    elif not headers_valid:
        reason = "not_a_diff: expected one allowlisted file with a hunk and changed lines"
    else:
        reason = None
    return PatchParseResult(
        valid=reason is None,
        reason=reason,
        allowed_paths=allowed,
        rejected_paths=rejected,
    )


def extract_diff(response: str) -> str:
    """Extract one fenced or plain unified diff without interpreting model prose."""
    if "```" in response:
        blocks = response.split("```")
        candidates = [
            block.partition("\n")[2]
            for block in blocks[1::2]
            if block.partition("\n")[0].strip() in ("", "diff", "patch")
        ]
        if len(candidates) != 1:
            raise ValueError("Expected exactly one diff block")
        return candidates[0].strip() + "\n"
    lines = response.splitlines()
    starts = [
        i
        for i, line in enumerate(lines)
        if line.startswith("diff --git ") or line.startswith("--- ")
    ]
    if not starts:
        raise ValueError("No unified diff in model response")
    diff = "\n".join(lines[starts[0] :])
    diff = diff.removesuffix("[MOCK_LLM_MODE - NEBIUS_API_KEY not set]").rstrip()
    return diff + "\n"


def apply_and_verify_patch(
    proposal: ProposedPatch,
    holdouts: list[dict] | None = None,
    witness_input: dict | None = None,
    contract: ContractBody | None = None,
    runner: RunnerAdapter | None = None,
    oracle: RepairOracle | None = None,
) -> ConstrainedPatchVerificationResult:
    """Execute exact allowed hunks in pinned workers; missing evidence never passes."""
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

    checks = [path_check]
    runs: list[dict] = []
    base_digest = patched_digest = None
    frozen_holdouts = copy.deepcopy(holdouts)
    frozen_witness = copy.deepcopy(witness_input)
    contract_digest = hash_contract(contract) if contract else None
    holdouts_digest = hashlib.sha256(
        json.dumps(frozen_holdouts, sort_keys=True).encode()
    ).hexdigest()
    witness_result = None
    results: list[HoldoutResult] = []
    measurement_checks: list[dict] = []
    oracle = copy.deepcopy(oracle)
    scientific_oracle = ({"status": "supplied", "sha256": oracle.digest(), "provenance": oracle.provenance}
                         if oracle else {"status": "not_supplied", "scope": "Compatibility checks only"})
    verdict = "inconclusive"
    limitations = [
        "Bounded curated numerical edits only; no arbitrary Python execution.",
        "Passing these checks is not proof of scientific correctness.",
        "An old/new agreement is not an independent scientific oracle. No version is automatically ground truth.",
    ]
    try:
        if runner is None or contract is None or not frozen_holdouts or frozen_witness is None:
            raise ValueError(
                "Runner, approved contract, witness and nonempty frozen holdouts are required"
            )
        sources = project_sources(Path(__file__).resolve().parents[2] / "fixture" / "project")
        base_digest = project_digest(sources)
        sources["pipeline.py"] = apply_diff(sources["pipeline.py"], proposal.diff_text)
        patched_digest = project_digest(sources)
        checks.append(
            PatchCheckResult(
                check_type="patch_application",
                status="pass",
                details="Exact hunks applied to a hashed project copy",
            )
        )
        for item in [frozen_witness, *frozen_holdouts]:
            if set(item) != {"signal"} or not validate_input(contract, item).valid:
                raise ValueError("Verification input violates the approved contract")

        def run_case(item: dict, case: str) -> HoldoutResult:
            sha = hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()
            outcomes: list[dict | None] = []
            for patched in (False, True):
                for kind in ("old", "new"):
                    manifest = RunManifest(
                        run_id=make_run_id(),
                        kind=kind,
                        candidate_sha256=sha,
                        entry_point="run.py",
                        input_json=copy.deepcopy(item),
                        timeout_seconds=min(contract.resource_limits.max_wall_seconds, 60),
                        max_memory_mb=min(contract.resource_limits.max_memory_mb, 512),
                        max_cpu_seconds=max(
                            1, min(int(contract.resource_limits.max_cpu_seconds), 60)
                        ),
                        patch_diff=proposal.diff_text if patched else None,
                        base_project_sha256=base_digest,
                        patched_project_sha256=patched_digest if patched else None,
                    )
                    record = {
                        "case": case,
                        "kind": kind,
                        "patched": patched,
                        "manifest": manifest.model_dump(mode="json"),
                    }
                    runs.append(record)
                    try:
                        result = runner.submit(manifest)
                        record["result"] = result.model_dump(mode="json")
                        measured = (
                            evaluate_output(result.outputs, contract)
                            if result.status == "ok"
                            else None
                        )
                        if measured and (
                            result.numpy_version != {"old": "1.26.4", "new": "2.0.2"}[kind]
                            or result.base_project_sha256 != base_digest
                            or result.patched_project_sha256
                            != (patched_digest if patched else None)
                        ):
                            measured = None
                        outcomes.append(measured)
                    except Exception as exc:  # noqa: BLE001
                        record["error"] = str(exc)
                        outcomes.append(None)
            conclusions = [value["conclusion"] if value else None for value in outcomes]
            old, new, pold, pnew = conclusions
            consistent = old is not None and new is not None and old == new
            measurement_preserved = True
            reference_passed = True
            reference_available = True
            if all(value is not None for value in outcomes):
                policy = contract.comparison
                if consistent:
                    measurement_preserved = all(preserve_measurement(
                        outcomes[index]["ratio"], outcomes[index + 2]["ratio"], policy.atol, policy.rtol
                    ) for index in (0, 1))
                if oracle:
                    reference = oracle.reference_by_input_sha256.get(sha)
                    reference_available = reference is not None
                    reference_passed = reference_available and all(preserve_measurement(
                        reference, outcomes[index]["ratio"], policy.atol, policy.rtol
                    ) for index in (2, 3))
            measurement_checks.append({
                "case": case, "input_sha256": sha,
                "baseline_ratios": [value["ratio"] if value else None for value in outcomes[:2]],
                "patched_ratios": [value["ratio"] if value else None for value in outcomes[2:]],
                "preservation": "not_run" if any(value is None for value in outcomes) else
                    "not_applicable_divergent_baseline" if not consistent else "pass" if measurement_preserved else "fail",
                "reference": "not_supplied" if oracle is None else "not_run" if not reference_available or any(value is None for value in outcomes)
                    else "pass" if reference_passed else "fail",
            })
            if any(value is None for value in outcomes) or not reference_available:
                status = "not_run"
            elif (
                pold != pnew
                or not measurement_preserved
                or not reference_passed
                or (consistent and old != pold)
                or contract.comparison.dtype_must_match
                and outcomes[2]["dtype"] != outcomes[3]["dtype"]
                or case == "witness"
                and old == new
            ):
                status = "fail"
            else:
                status = "pass"
            return HoldoutResult(
                input_json=item,
                input_sha256=sha,
                old_conclusion=old,
                new_conclusion=new,
                conclusion_consistent=consistent,
                patched_old_conclusion=pold,
                patched_new_conclusion=pnew,
                check_status=status,
            )

        witness_result = run_case(frozen_witness, "witness")
        results = [run_case(item, f"holdout_{i}") for i, item in enumerate(frozen_holdouts)]
        statuses = [witness_result.check_status, *(item.check_status for item in results)]
        verdict = (
            "repair_fails_stated_checks"
            if "fail" in statuses
            else "inconclusive"
            if "not_run" in statuses
            else "repair_passes_stated_checks"
        )
        checks.append(
            PatchCheckResult(
                check_type="patch_execution",
                status="not_run" if "not_run" in statuses else "pass",
                details=f"{len(runs)} recorded runs across the witness and frozen holdouts",
            )
        )
        if "not_run" in statuses:
            limitations.append(
                "Some outputs were unavailable, invalid or did not establish the pinned NumPy versions."
            )
    except (ValueError, SyntaxError, OSError) as exc:
        checks.append(
            PatchCheckResult(check_type="patch_execution", status="not_run", details=str(exc))
        )
        limitations.append(str(exc))
        if (
            runner is not None
            and contract is not None
            and frozen_holdouts
            and frozen_witness is not None
            and patched_digest is None
        ):
            verdict = "invalid_patch"
    return ConstrainedPatchVerificationResult(
        proposal=proposal,
        parse_result=parsed,
        holdout_results=results,
        measurement_checks=measurement_checks,
        scientific_oracle=scientific_oracle,
        witness_result=witness_result,
        execution_runs=runs,
        patch_checks=checks,
        verdict=verdict,
        limitations=limitations,
        base_project_sha256=base_digest,
        patched_project_sha256=patched_digest,
        contract_sha256=contract_digest,
        holdouts_sha256=holdouts_digest,
    )
