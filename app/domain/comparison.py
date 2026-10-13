"""Evaluate the curated numeric output under the approved contract.

Worker classifications are observations, not the authority for a decision rule.
Exact ties use the contract's tie policy; comparison tolerances describe numeric
drift only and never move the decision boundary.
"""
from __future__ import annotations

import math
import operator

from app.domain.contract import ContractBody

_OPERATORS = {
    "gt": operator.gt, "gte": operator.ge, "lt": operator.lt,
    "lte": operator.le, "eq": operator.eq, "ne": operator.ne,
}


def evaluate_output(outputs: dict, contract: ContractBody) -> dict:
    """Validate one fixture result and compute its declared conclusion.

This release supports the fixture's ``ratio`` measurement and ``conclusion``
selector only. Unsupported selectors fail explicitly rather than silently
interpreting an unrelated measurement.
"""
    if not isinstance(outputs, dict):
        raise ValueError("Worker outputs must be an object")
    if outputs.get("status", "ok") != "ok":
        raise ValueError("Worker output reports an execution failure")
    result = outputs.get("result", outputs)
    if not isinstance(result, dict):
        raise ValueError("Worker result must be an object")
    if contract.output_selector.field_name != "conclusion":
        raise ValueError("Unsupported output selector for the curated ratio evaluator")
    reported = result.get("conclusion")
    if reported not in ("above_threshold", "below_threshold", "inconclusive"):
        raise ValueError("Missing or invalid worker conclusion")
    value = result.get("ratio")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Missing or invalid numeric ratio")
    ratio = float(value)
    threshold = contract.conclusion.threshold
    if not math.isfinite(ratio) or not math.isfinite(threshold):
        raise ValueError("Ratio and decision threshold must be finite")
    rule = contract.conclusion
    if ratio == threshold:
        conclusion = {
            "above": "above_threshold", "below": "below_threshold",
            "inconclusive": "inconclusive",
        }[rule.tie_behavior]
    else:
        conclusion = "above_threshold" if _OPERATORS[rule.operator](ratio, threshold) else "below_threshold"
    dtype = result.get("intermediate_dtype")
    if contract.comparison.dtype_must_match and (not isinstance(dtype, str) or not dtype):
        raise ValueError("Missing intermediate dtype required by comparison policy")
    return {"conclusion": conclusion, "ratio": ratio, "reported_conclusion": reported, "dtype": dtype}


def numeric_matches(old: float, new: float, contract: ContractBody) -> bool:
    """Use the new result as reference: |old-new| <= atol + rtol*|new|."""
    policy = contract.comparison
    if policy.atol < 0 or policy.rtol < 0 or not math.isfinite(policy.atol + policy.rtol):
        raise ValueError("Comparison tolerances must be finite and non-negative")
    return abs(old - new) <= policy.atol + policy.rtol * abs(new)
