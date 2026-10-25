"""Small, explicit transfer summary for an untested project hypothesis."""

from __future__ import annotations

import hashlib
import json
import math

_CONCLUSIONS = {"above_threshold", "below_threshold", "inconclusive"}


def model_summary(job: dict) -> dict:
    """Allowlist executed numbers only; never send source, repo or raw inputs."""
    if job.get("status") != "completed":
        raise ValueError("A completed investigation is required")
    finding = job.get("result_json") or {}
    if (finding.get("kind") not in {"approved_project_search", "approved_project_sample"}
            or finding.get("changed_conclusion") is not True
            or finding.get("inconclusive") is True):
        raise ValueError("A repeatable changed outcome is required")
    observed = finding.get("witness") if finding["kind"] == "approved_project_search" else finding
    if not isinstance(observed, dict) or observed.get("repeatable") is not True:
        raise ValueError("The witness is unavailable")

    def measurement(name: str) -> dict:
        value = observed.get(name)
        if not isinstance(value, dict):
            raise ValueError("The repeated measurement is unavailable")
        ratio = value.get("ratio")
        conclusion = value.get("conclusion")
        try:
            finite = not isinstance(ratio, bool) and isinstance(ratio, (int, float)) and math.isfinite(float(ratio))
        except OverflowError:
            finite = False
        if not finite or conclusion not in _CONCLUSIONS:
            raise ValueError("The repeated measurement is invalid")
        return {"measurement": float(ratio), "declared_outcome": conclusion}

    summary = {
        "case": "approved_project_dependency_change",
        "dependency": "NumPy 1.26.4 to 2.0.2",
        "source_scope": "same immutable source in both environments",
        "tested_inputs": finding.get("attempted", 1),
        "old": measurement("old"),
        "old_repeat": measurement("old_repeat"),
        "new": measurement("new"),
        "new_repeat": measurement("new_repeat"),
        "evidence_status": "repeatable_changed_declared_outcome",
        "mechanism_status": "untested",
        "repair_status": "untested",
    }
    if (type(summary["tested_inputs"]) is not int
            or not 1 <= summary["tested_inputs"] <= 32
            or summary["old"]["declared_outcome"] != summary["old_repeat"]["declared_outcome"]
            or summary["new"]["declared_outcome"] != summary["new_repeat"]["declared_outcome"]
            or summary["old"]["declared_outcome"] == summary["new"]["declared_outcome"]):
        raise ValueError("Saved evidence does not support a repeatable changed outcome")
    if len(json.dumps(summary, allow_nan=False)) > 2_048:
        raise ValueError("Model summary exceeds its bound")
    return summary


def summary_digest(summary: dict) -> str:
    return hashlib.sha256(json.dumps(summary, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()
