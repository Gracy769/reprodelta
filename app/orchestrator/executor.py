"""
Orchestrator: paired execution engine.

Submits one run against 'old' and one against 'new', then compares outputs
using the contract's output_selector and ComparisonPolicy.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.domain.comparison import evaluate_output, numeric_matches
from app.domain.contract import ContractBody
from app.domain.types import VerdictKind
from app.providers.runner import RunManifest, RunnerAdapter, RunResult, make_run_id


def _utcnow() -> datetime:
    return datetime.now(UTC)


class PairedRunRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    investigation_id: UUID
    candidate: dict
    candidate_sha256: str
    contract: ContractBody


class SingleRunRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    environment: str
    status: str
    outputs: dict
    stderr: str
    run_id: str
    started_at: datetime
    ended_at: datetime
    numpy_version: str | None


class PairedRunResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate_sha256: str
    old_run: SingleRunRecord
    new_run: SingleRunRecord
    comparison: dict

    @property
    def old_conclusion(self) -> str | None:
        """Convenience accessor for comparison['old_conclusion']."""
        return self.comparison.get("old_conclusion")

    @property
    def new_conclusion(self) -> str | None:
        """Convenience accessor for comparison['new_conclusion']."""
        return self.comparison.get("new_conclusion")

    @property
    def conclusion_changed(self) -> bool | None:
        """Convenience accessor for comparison['conclusion_changed']."""
        return self.comparison.get("conclusion_changed")


def _extract_numpy_version(outputs: dict) -> str | None:
    """Best-effort extraction of numpy_version from nested runner output."""
    # The fixture returns {"status": "ok", "result": {..., "numpy_version": "..."}}
    result = outputs.get("result")
    if isinstance(result, dict):
        return result.get("numpy_version")
    return outputs.get("numpy_version")


def _extract_conclusion(outputs: dict, field_name: str) -> Any:
    """Extract the named conclusion field from runner outputs.

    The fixture returns: outputs = {"status": "ok", "result": {"conclusion": ..., "ratio": ...}}
    We look inside "result" first, then at top level.
    """
    result = outputs.get("result")
    if isinstance(result, dict) and field_name in result:
        return result[field_name]
    return outputs.get(field_name)


def _extract_ratio(outputs: dict) -> float | None:
    result = outputs.get("result")
    if isinstance(result, dict):
        v = result.get("ratio")
        return float(v) if v is not None else None
    return None


def compare_outputs(old_out: dict, new_out: dict, contract: ContractBody) -> dict:
    """Validate outputs before comparing contract-derived conclusions."""
    old_status = old_out.get("status", "error")
    new_status = new_out.get("status", "error")
    failure = {
        "old_conclusion": None, "new_conclusion": None,
        "old_ratio": None, "new_ratio": None, "conclusion_changed": None,
        "verdict_hint": VerdictKind.setup_failure,
        "old_status": old_status, "new_status": new_status,
    }
    if old_status != "ok" or new_status != "ok":
        return {**failure, "reason": "paired_execution_failed"}
    try:
        old = evaluate_output(old_out.get("outputs"), contract)
        new = evaluate_output(new_out.get("outputs"), contract)
        numeric_equal = numeric_matches(old["ratio"], new["ratio"], contract)
    except (ValueError, TypeError, OverflowError) as exc:
        return {**failure, "reason": str(exc)}
    changed = old["conclusion"] != new["conclusion"]
    dtype_equal = old["dtype"] == new["dtype"]
    if changed:
        verdict = VerdictKind.supported_conclusion_change
    elif old["conclusion"] == "inconclusive":
        verdict = VerdictKind.inconclusive
    elif not numeric_equal or (contract.comparison.dtype_must_match and not dtype_equal):
        verdict = VerdictKind.observed_drift
    else:
        verdict = VerdictKind.no_violation_found
    return {
        "old_conclusion": old["conclusion"], "new_conclusion": new["conclusion"],
        "old_reported_conclusion": old["reported_conclusion"],
        "new_reported_conclusion": new["reported_conclusion"],
        "old_ratio": old["ratio"], "new_ratio": new["ratio"],
        "conclusion_changed": changed, "numeric_equal": numeric_equal,
        "dtype_equal": dtype_equal, "verdict_hint": verdict,
    }


def _run_result_to_record(result: RunResult, environment: str) -> SingleRunRecord:
    now = _utcnow()
    return SingleRunRecord(
        environment=environment,
        status=result.status,
        outputs=result.outputs,
        stderr=result.stderr,
        run_id=result.run_id,
        started_at=now,
        ended_at=now,
        numpy_version=_extract_numpy_version(result.outputs),
    )


def run_pair(request: PairedRunRequest, runner: RunnerAdapter) -> PairedRunResult:
    """Submit old and new runs, compare outputs, return PairedRunResult."""
    entry_point = "run.py"

    old_manifest = RunManifest(
        run_id=make_run_id(),
        kind="old",
        candidate_sha256=request.candidate_sha256,
        entry_point=entry_point,
        input_json=request.candidate,
        timeout_seconds=request.contract.resource_limits.max_wall_seconds,
    )
    new_manifest = RunManifest(
        run_id=make_run_id(),
        kind="new",
        candidate_sha256=request.candidate_sha256,
        entry_point=entry_point,
        input_json=request.candidate,
        timeout_seconds=request.contract.resource_limits.max_wall_seconds,
    )

    old_result = runner.submit(old_manifest)
    new_result = runner.submit(new_manifest)

    old_record = _run_result_to_record(old_result, "old")
    new_record = _run_result_to_record(new_result, "new")

    # Build dicts that compare_outputs expects: {"status": ..., "outputs": ...}
    old_out = {"status": old_result.status, "outputs": old_result.outputs}
    new_out = {"status": new_result.status, "outputs": new_result.outputs}
    comparison = compare_outputs(old_out, new_out, request.contract)

    return PairedRunResult(
        candidate_sha256=request.candidate_sha256,
        old_run=old_record,
        new_run=new_record,
        comparison=comparison,
    )


def run_control(
    input_json: dict,
    env: str,
    contract: ContractBody,
    runner: RunnerAdapter,
    candidate_sha256: str,
    n: int = 2,
) -> list[RunResult]:
    """Run the same input *n* times in the same environment to verify repeatability (F4)."""
    kind: Any = env  # "old" or "new"
    results = []
    for _ in range(n):
        manifest = RunManifest(
            run_id=make_run_id(),
            kind=kind,
            candidate_sha256=candidate_sha256,
            entry_point="run.py",
            input_json=input_json,
            timeout_seconds=contract.resource_limits.max_wall_seconds,
        )
        results.append(runner.submit(manifest))
    return results
