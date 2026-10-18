"""
Validates candidate inputs against an approved contract.
Returns a ValidationResult with status and reason.
Never executes project code.
"""
from __future__ import annotations

import math

from pydantic import BaseModel, ConfigDict

from app.domain.contract import ContractBody


class ValidationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    valid: bool
    reason: str | None = None
    checked_fields: list[str] = []


def validate_input(contract: ContractBody, input_json: dict) -> ValidationResult:
    """Validate *input_json* against the contract's input_schema.

    Checks (in order):
    1. All required fields are present.
    2. Each field value is a list (sequence).
    3. Shape matches the declared shape.
    4. All elements are numbers (int/float).
    5. Value range (min_value / max_value).
    6. NaN policy.
    7. Inf policy.
    """
    checked: list[str] = []

    for field in contract.input_schema.fields:
        fname = field.name

        # 1. Presence
        if fname not in input_json:
            return ValidationResult(
                valid=False,
                reason=f"Missing required field: '{fname}'",
                checked_fields=checked,
            )

        value = input_json[fname]

        # 2. Must be a list / sequence (not a bare scalar or dict)
        if not isinstance(value, (list, tuple)):
            return ValidationResult(
                valid=False,
                reason=f"Field '{fname}' must be a list, got {type(value).__name__}",
                checked_fields=checked,
            )

        # Validate every dimension, including ragged arrays, before flattening.
        def flatten(items, shape):
            if not shape:
                return [items]
            if not isinstance(items, (list, tuple)) or len(items) != shape[0]:
                raise ValueError("shape mismatch")
            return [item for child in items for item in flatten(child, shape[1:])]
        try:
            if not 1 <= len(field.shape) <= 4 or any(dimension < 1 or dimension > 1000 for dimension in field.shape):
                raise ValueError("unsupported shape")
            value = flatten(value, field.shape)
        except ValueError:
            return ValidationResult(valid=False, reason=f"Field '{fname}' has wrong length or shape: expected {field.shape}", checked_fields=checked)

        # 4. All elements must be numeric
        for i, elem in enumerate(value):
            if type(elem) not in (int, float):
                return ValidationResult(
                    valid=False,
                    reason=(
                        f"Field '{fname}' element [{i}] is not a number: "
                        f"{type(elem).__name__}"
                    ),
                    checked_fields=checked,
                )

        # 5/6/7. Per-element range + NaN + Inf
        for i, elem in enumerate(value):
            elem_f = float(elem)

            if not field.allow_nan and math.isnan(elem_f):
                return ValidationResult(
                    valid=False,
                    reason=f"Field '{fname}' element [{i}] is NaN (not allowed by contract)",
                    checked_fields=checked,
                )

            if not field.allow_inf and math.isinf(elem_f):
                return ValidationResult(
                    valid=False,
                    reason=f"Field '{fname}' element [{i}] is Inf (not allowed by contract)",
                    checked_fields=checked,
                )

            # Skip range check for NaN/Inf that are explicitly allowed
            if math.isnan(elem_f) or math.isinf(elem_f):
                continue

            if field.min_value is not None and elem_f < field.min_value:
                return ValidationResult(
                    valid=False,
                    reason=(
                        f"Field '{fname}' element [{i}] = {elem_f} is below "
                        f"min_value {field.min_value}"
                    ),
                    checked_fields=checked,
                )

            if field.max_value is not None and elem_f > field.max_value:
                return ValidationResult(
                    valid=False,
                    reason=(
                        f"Field '{fname}' element [{i}] = {elem_f} is above "
                        f"max_value {field.max_value}"
                    ),
                    checked_fields=checked,
                )

        checked.append(fname)

    from app.domain.constraints import check_constraints

    failure = check_constraints(contract.input_schema.cross_constraints or [], input_json)
    if failure:
        return ValidationResult(valid=False, reason=failure, checked_fields=checked)
    return ValidationResult(valid=True, reason=None, checked_fields=checked)
