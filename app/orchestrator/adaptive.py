"""Seeded feedback search without fixture inputs, holdouts or model calls."""

import math
import random

import numpy as np


def adaptive_candidates(contract, seed, count, records):
    fields = contract.input_schema.fields
    for field in fields:
        if (len(field.shape) != 1 or not 1 <= field.shape[0] <= 1000
                or field.min_value is None or field.max_value is None
                or not math.isfinite(field.min_value + field.max_value)
                or field.min_value > field.max_value):
            raise ValueError("Adaptive search requires bounded one-dimensional fields")
        if field.dtype not in {"float32", "float64"}:
            raise ValueError("Adaptive search supports float32 and float64 fields")
    rng = random.Random(seed)
    threshold = contract.conclusion.threshold
    if not math.isfinite(threshold):
        raise ValueError("Adaptive search requires a finite decision threshold")
    for step in range(count):
        measured = [item for item in records if item.pair_result is not None
                    and item.pair_result.comparison.get("old_ratio") is not None
                    and item.pair_result.comparison.get("new_ratio") is not None]
        if not measured or step % 5 == 0:
            candidate = {field.name: [float(np.asarray(
                rng.uniform(field.min_value, field.max_value), dtype=field.dtype
            )) for _ in range(field.shape[0])] for field in fields}
        else:
            above = [item for item in measured if item.pair_result.comparison["old_ratio"] > threshold]
            below = [item for item in measured if item.pair_result.comparison["old_ratio"] < threshold]
            if above and below and step % 4 != 0:
                left = min(above, key=lambda item: item.pair_result.comparison["old_ratio"] - threshold)
                right = min(below, key=lambda item: threshold - item.pair_result.comparison["old_ratio"])
                # Bisection is an experiment, not a monotonicity assumption or
                # proof. Every interpolated candidate is still domain-validated.
                yield {field.name: [float(np.asarray((a + b) / 2, dtype=field.dtype))
                                    for a, b in zip(left.input_json[field.name], right.input_json[field.name], strict=True)]
                       for field in fields}
                continue
            best = min(measured, key=lambda item: min(
                abs(item.pair_result.comparison[key] - threshold)
                for key in ("old_ratio", "new_ratio")
            ))
            candidate = {key: list(values) for key, values in best.input_json.items()}
            field = rng.choice(fields)
            index = rng.randrange(field.shape[0])
            old = candidate[field.name][index]
            if step % 3 == 0:
                proposed = float(np.nextafter(np.asarray(old, dtype=field.dtype),
                                             np.asarray(rng.choice([field.min_value, field.max_value]), dtype=field.dtype)))
            else:
                scale = (field.max_value - field.min_value) * 2 ** (-min(24, 1 + step // 3))
                proposed = old + rng.uniform(-scale, scale)
            candidate[field.name][index] = float(np.asarray(
                min(field.max_value, max(field.min_value, proposed)), dtype=field.dtype
            ))
        yield candidate
