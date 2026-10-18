"""A bounded declarative predicate language; never execute contract text."""

from __future__ import annotations

import math
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator


class CrossConstraint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["sum_close", "same_shape", "symmetric", "positive_definite", "nondecreasing"]
    fields: list[str] = Field(min_length=1, max_length=2)
    target: float = 0.0
    atol: float = Field(default=0.0, ge=0)

    @model_validator(mode="after")
    def bounded(self):
        if not math.isfinite(self.target) or not math.isfinite(self.atol):
            raise ValueError("Predicate parameters must be finite")
        if len(self.fields) != (2 if self.kind == "same_shape" else 1):
            raise ValueError("Wrong number of predicate fields")
        if any(not name.isidentifier() or len(name) > 80 for name in self.fields):
            raise ValueError("Predicate fields must be simple identifiers")
        return self


def check_constraints(constraints: list, inputs: dict) -> str | None:
    """Return an explicit failure; unknown strings are never assumed satisfied."""
    if len(constraints) > 32:
        return "At most 32 cross-constraints are supported"
    for raw in constraints:
        try:
            rule = (CrossConstraint.model_validate_json(raw) if isinstance(raw, str)
                    else CrossConstraint.model_validate(raw))
            arrays = [np.asarray(inputs[name], dtype=np.float64) for name in rule.fields]
            if any(array.size > 4096 or not np.isfinite(array).all() for array in arrays):
                raise ValueError("Predicates require at most 4096 finite values per field")
            value = arrays[0]
            if rule.kind == "same_shape":
                passed = value.shape == arrays[1].shape
            elif rule.kind == "sum_close":
                passed = abs(math.fsum(value.flat) - rule.target) <= rule.atol
            elif rule.kind == "nondecreasing":
                passed = value.ndim == 1 and bool(np.all(np.diff(value) >= -rule.atol))
            else:
                square = value.ndim == 2 and value.shape[0] == value.shape[1]
                symmetric = square and bool(np.allclose(value, value.T, atol=rule.atol, rtol=0))
                passed = symmetric
                if rule.kind == "positive_definite":
                    passed = symmetric and value.shape[0] <= 64 and bool(
                        np.linalg.eigvalsh(value).min() > rule.atol
                    )
            if not passed:
                return f"Cross-constraint {rule.kind} failed for {', '.join(rule.fields)}"
        except (ValueError, TypeError, KeyError, OverflowError, np.linalg.LinAlgError) as exc:
            return f"Unsupported or invalid cross-constraint: {exc}"
    return None
