"""Compare recorded checkpoints; a first observed difference is not a cause."""

import math


def localize_checkpoints(old_outputs: dict, new_outputs: dict) -> dict:
    before, after = old_outputs.get("checkpoints", []), new_outputs.get("checkpoints", [])
    if not before or not after:
        return {"status": "unavailable", "limitations": ["Both recorded checkpoint traces are required."]}
    producers = [old_outputs.get("trace_producer_sha256"), new_outputs.get("trace_producer_sha256")]
    if not all(producers) or producers[0] != producers[1]:
        return {"status": "inconclusive", "trace_producers": producers,
                "limitations": ["Checkpoint instrumentation digests are missing or differ; traces cannot establish localization."]}
    if [item["name"] for item in before] != [item["name"] for item in after]:
        return {"status": "inconclusive", "trace_producers": producers,
                "limitations": ["The two executions reached different checkpoint sets; no aligned first difference is claimed."]}
    right = {item["name"]: item for item in after}
    rows = []
    for left in before:
        other = right.get(left["name"])
        if other is None:
            continue
        rows.append({"checkpoint": left["name"], "old": left, "new": other,
                     "dtype_changed": left["dtype"] != other["dtype"],
                     "value_changed": left["value"] != other["value"],
                     "shape_changed": left["shape"] != other["shape"]})
    first = next((row["checkpoint"] for row in rows if any(
        row[key] for key in ("dtype_changed", "value_changed", "shape_changed")
    )), None)
    return {"status": "observed", "first_observed_difference": first, "checkpoints": rows,
            "trace_producers": producers,
            "limitations": ["Only named checkpoints in the trusted pipeline were observed.",
                            "Exact value/dtype differences are localization evidence, not causal proof.",
                            "Mixed source/environment attribution requires the deferred project runner."]}


def search_summary(progress) -> dict:
    return {"strategy": progress.search_strategy, "seed": progress.seed,
            "attempted": progress.attempted, "valid": progress.valid, "invalid": progress.invalid,
            "stop_reason": progress.stop_reason or progress.status,
            "algorithm": "feedback-boundary-v1" if progress.search_strategy == "adaptive" else progress.search_strategy,
            "candidates": [{"input_sha256": candidate.input_sha256,
                            "input": candidate.input_json, "validity": candidate.validity,
                            "reason": candidate.rejection_reason,
                            "comparison": candidate.pair_result.comparison if candidate.pair_result else None}
                           for candidate in progress.candidates],
            "limitations": ["Adaptive search uses measured threshold distance; no model calls or holdouts.",
                            "Finite search cannot establish absence of a regression."]}


def preserve_measurement(baseline: float, patched: float, atol: float, rtol: float) -> bool:
    return (math.isfinite(baseline) and math.isfinite(patched)
            and abs(patched - baseline) <= atol + rtol * abs(baseline))
