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
    2. Each field matches the declared shape.
    3. All elements are numeric and fit the declared dtype.
    4. Values satisfy the range, NaN and Inf policy.
    """
    checked: list[str] = []

    if not isinstance(input_json, dict):
        return ValidationResult(valid=False, reason="Input must be a JSON object")

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

        # Scalar fields use shape=[]; arrays keep the existing list contract.
        if field.shape and not isinstance(value, (list, tuple)):
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
            if not 0 <= len(field.shape) <= 4 or any(dimension < 1 or dimension > 1000 for dimension in field.shape):
                raise ValueError("unsupported shape")
            if not field.shape and type(value) not in (int, float):
                raise ValueError("scalar field must be numeric")
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

        # A declared integer must not quietly accept a fractional float. This
        # also prevents a later NumPy cast from changing the approved input.
        integer_limits = {"int32": (-2**31, 2**31 - 1),
                          "int64": (-2**63, 2**63 - 1)}
        float32_limit = 3.4028234663852886e38

        # 5/6. Dtype, representability, range, NaN and Inf.
        for i, elem in enumerate(value):
            if field.dtype in integer_limits:
                low, high = integer_limits[field.dtype]
                if type(elem) is not int or not low <= elem <= high:
                    return ValidationResult(
                        valid=False,
                        reason=f"Field '{fname}' element [{i}] is not a representable {field.dtype}",
                        checked_fields=checked,
                    )
            try:
                elem_f = float(elem)
            except OverflowError:
                return ValidationResult(
                    valid=False,
                    reason=f"Field '{fname}' element [{i}] exceeds the numeric dtype",
                    checked_fields=checked,
                )
            if field.dtype == "float32" and math.isfinite(elem_f) and abs(elem_f) > float32_limit:
                return ValidationResult(
                    valid=False,
                    reason=f"Field '{fname}' element [{i}] exceeds float32 range",
                    checked_fields=checked,
                )

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
