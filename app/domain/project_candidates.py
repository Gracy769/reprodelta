"""Deterministic, contract-checked inputs for the approved-project pilot."""

from __future__ import annotations

import copy
import json
import math
import random
from typing import Literal

from app.domain.contract import ContractBody, InputField
from app.domain.validator import validate_input

MAX_CANDIDATES = 32


def _bounds(field: InputField) -> tuple[int | float, int | float] | None:
    if field.min_value is None or field.max_value is None:
        return None
    if not math.isfinite(field.min_value) or not math.isfinite(field.max_value):
        return None
    if field.dtype in {"int32", "int64"}:
        low, high = math.ceil(field.min_value), math.floor(field.max_value)
        limit = 2**31 if field.dtype == "int32" else 2**63
        low, high = max(low, -limit), min(high, limit - 1)
    else:
        low, high = field.min_value, field.max_value
        if field.dtype == "float32":
            limit = 3.4028234663852886e38
            low, high = max(low, -limit), min(high, limit)
    return (low, high) if low <= high else None


def _changed(sample: dict, field: InputField, index: int | None, value: int | float) -> dict:
    candidate = copy.deepcopy(sample)
    if index is None:
        candidate[field.name] = value
    else:
        candidate[field.name][index] = value
    return candidate


def candidate_inputs(
    contract: ContractBody,
    sample: dict,
    *,
    strategy: Literal["sweep", "seeded_random"],
    budget: int,
    seed: int = 0,
) -> list[dict]:
    """Return at most `budget` unique valid candidates, including the sample.

    This generates inputs only. Execution and cost accounting are separate;
    callers must validate every candidate again against the approved contract.
    """
    if not 1 <= budget <= MAX_CANDIDATES:
        raise ValueError("Candidate budget must be within 1–32")
    if not isinstance(sample, dict) or not validate_input(contract, sample).valid:
        raise ValueError("The approved sample does not satisfy its contract")
    if set(sample) != {field.name for field in contract.input_schema.fields}:
        raise ValueError("The approved sample contains undeclared fields")
    if strategy not in {"sweep", "seeded_random"}:
        raise ValueError("Unsupported candidate strategy")
    results = [copy.deepcopy(sample)]
    seen = {json.dumps(sample, sort_keys=True, separators=(",", ":"), allow_nan=False)}

    def add(candidate: dict) -> None:
        if len(results) >= budget or not validate_input(contract, candidate).valid:
            return
        encoded = json.dumps(candidate, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if encoded not in seen:
            seen.add(encoded)
            results.append(candidate)

    coordinates = []
    for field in contract.input_schema.fields:
        bounds = _bounds(field)
        if bounds is None or len(field.shape) > 1:
            continue
        indices = range(field.shape[0]) if field.shape else (None,)
        coordinates.extend((field, index, bounds) for index in indices)

    if strategy == "sweep":
        for field, index, (low, high) in coordinates:
            midpoint = (low + high) / 2
            if field.dtype in {"int32", "int64"}:
                midpoint = int(round(midpoint))
            for value in (low, midpoint, high):
                add(_changed(sample, field, index, value))
                if len(results) >= budget:
                    return results
    elif coordinates:
        rng = random.Random(seed)
        for _ in range(budget * 16):
            field, index, (low, high) = rng.choice(coordinates)
            value = (rng.randint(low, high) if field.dtype in {"int32", "int64"}
                     else rng.uniform(low, high))
            add(_changed(sample, field, index, value))
            if len(results) >= budget:
                break
    return results


def feedback_candidates(
    contract: ContractBody,
    sample: dict,
    observations: list[tuple[dict, float, float]],
    *,
    budget: int,
    seed: int = 0,
) -> list[dict]:
    """Use measured distance from the approved decision boundary to choose inputs.

    Feedback is numeric execution data, never an untrusted model suggestion.
    Each proposal changes one declared coordinate and is checked again by the
    same immutable input contract before it can be returned to the worker.
    """
    if not 0 <= budget <= MAX_CANDIDATES:
        raise ValueError("Feedback budget must be within 0–32")
    if not observations or budget == 0:
        return []
    threshold = contract.conclusion.threshold
    if not math.isfinite(threshold):
        raise ValueError("Decision threshold must be finite")
    valid = []
    seen = set()
    for candidate, old, new in observations:
        if (not isinstance(candidate, dict) or not validate_input(contract, candidate).valid
                or not math.isfinite(old) or not math.isfinite(new)):
            raise ValueError("Feedback requires contract-valid inputs and finite measurements")
        encoded = json.dumps(candidate, sort_keys=True, separators=(",", ":"), allow_nan=False)
        seen.add(encoded)
        valid.append((min(abs(old - threshold), abs(new - threshold)), candidate))
    valid.sort(key=lambda item: item[0])
    anchors = [item[1] for item in valid]
    coordinates = []
    for field in contract.input_schema.fields:
        bounds = _bounds(field)
        if bounds is None or len(field.shape) > 1:
            continue
        indices = range(field.shape[0]) if field.shape else (None,)
        coordinates.extend((field, index, bounds) for index in indices)
    rng = random.Random(seed)
    rng.shuffle(coordinates)
    results = []

    def add(candidate: dict) -> None:
        if len(results) >= budget or not validate_input(contract, candidate).valid:
            return
        encoded = json.dumps(candidate, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if encoded not in seen:
            seen.add(encoded)
            results.append(candidate)

    for anchor in anchors:
        for field, index, (low, high) in coordinates:
            value = anchor[field.name] if index is None else anchor[field.name][index]
            for target in (low, high, sample[field.name] if index is None else sample[field.name][index]):
                for fraction in (0.5, 0.25, 0.75):
                    proposal = value + (target - value) * fraction
                    if field.dtype in {"int32", "int64"}:
                        proposal = int(round(proposal))
                    add(_changed(anchor, field, index, proposal))
                    if len(results) >= budget:
                        return results
    return results
