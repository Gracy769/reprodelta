"""Small public copy of an approved-project result with a strict field allowlist."""

from __future__ import annotations

import math


def _bounded_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and 0 <= value <= 1_000_000 else None


def redacted_project_share(job: dict) -> dict:
    if job.get("status") != "completed":
        raise ValueError("Only completed project runs can be shared")
    result = job.get("result_json") or {}
    kind = result.get("kind")
    if kind not in {"approved_project_sample", "approved_project_search"}:
        raise ValueError("This run has no shareable approved-project finding")

    observations = result.get("observations") if kind == "approved_project_search" else None
    if observations is not None and not isinstance(observations, list):
        raise ValueError("The saved observation list is invalid")
    summary = []
    for item in (observations or [])[:32]:
        if not isinstance(item, dict):
            raise ValueError("The saved observation list is invalid")
        summary.append({
            "repeatable": item.get("repeatable") is True,
            "changed_conclusion": item.get("changed_conclusion") is True,
        })
    return {
        "schema_version": 1,
        "kind": kind,
        "scope": (
            "Bounded candidate search at one dependency-only change"
            if kind == "approved_project_search"
            else "One approved sample at one dependency-only change"
        ),
        "tested_inputs": min(32, len(summary)) if observations is not None else 1,
        "changed_conclusion": result.get("changed_conclusion") is True,
        "inconclusive": result.get("inconclusive") is True,
        "repeatability_tested": observations is not None or isinstance(result.get("repeatable"), bool),
        "observations": summary,
        "sandbox_seconds": _bounded_number(result.get("sandbox_seconds")),
        "estimated_sandbox_cost_usd": _bounded_number(result.get("estimated_sandbox_cost_usd")),
        "limitations": [
            "The public copy omits repository identity, commit IDs, source files, input values, hashes, and numeric measurements.",
            "Mechanism and repair were not established by this approved-project run.",
            "A negative result means no violation was found within the tested inputs and budget.",
        ],
    }
