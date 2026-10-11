"""
Contract domain types.

A Contract defines:
- input_schema: valid input ranges, dtypes, shapes, constraints
- output_selector: which output field to evaluate
- conclusion: the decision rule (operator, threshold, units, tie behavior)
- comparison: tolerance policy, NaN/Inf policy, dtype policy
- resource_limits: per-run CPU/memory/time bounds
- assumptions: plain-text list
- review_provenance: model/human who proposed it

The contract body_json is canonicalized to bytes and SHA-256 hashed.
The hash covers the approved semantics, not formatting.
"""
from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict


class InputField(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    dtype: str
    shape: list[int]
    min_value: float | None = None
    max_value: float | None = None
    allow_nan: bool = False
    allow_inf: bool = False


class InputSchema(BaseModel):
    model_config = ConfigDict(frozen=True)

    fields: list[InputField]
    cross_constraints: list[str] | None = None


class OutputSelector(BaseModel):
    model_config = ConfigDict(frozen=True)

    field_name: str
    dtype: str | None = None
    units: str | None = None


class ConclusionRule(BaseModel):
    model_config = ConfigDict(frozen=True)

    operator: Literal["gt", "gte", "lt", "lte", "eq", "ne"]
    threshold: float
    units: str | None = None
    tie_behavior: Literal["above", "below", "inconclusive"] = "inconclusive"


class ComparisonPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    atol: float = 1e-7
    rtol: float = 1e-5
    nan_equal: bool = False
    inf_equal: bool = True
    dtype_must_match: bool = False


class ResourceLimits(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_wall_seconds: int = 30
    max_memory_mb: int = 512
    max_cpu_seconds: float = 25.0


class ContractBody(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal["1"] = "1"
    input_schema: InputSchema
    output_selector: OutputSelector
    conclusion: ConclusionRule
    comparison: ComparisonPolicy = ComparisonPolicy()
    resource_limits: ResourceLimits = ResourceLimits()
    assumptions: list[str] = []
    review_provenance: str = "unreviewed"


def canonicalize_contract(body: ContractBody) -> bytes:
    """Return stable JSON bytes for a contract (sorted keys, no whitespace)."""
    return json.dumps(body.model_dump(), sort_keys=True, separators=(",", ":")).encode()


def hash_contract(body: ContractBody) -> str:
    """Return the SHA-256 hex digest of the canonical contract bytes."""
    return hashlib.sha256(canonicalize_contract(body)).hexdigest()


# ---------------------------------------------------------------------------
# Default contract for the curated fixture
# ---------------------------------------------------------------------------

FIXTURE_CONTRACT = ContractBody(
    input_schema=InputSchema(
        fields=[
            InputField(
                name="signal",
                dtype="float32",
                shape=[8],
                min_value=-10.0,
                max_value=10.0,
                allow_nan=False,
                allow_inf=False,
            )
        ]
    ),
    output_selector=OutputSelector(field_name="conclusion", units=None),
    conclusion=ConclusionRule(operator="gt", threshold=0.5, units=None),
    comparison=ComparisonPolicy(atol=1e-7, rtol=1e-5),
    resource_limits=ResourceLimits(max_wall_seconds=10, max_memory_mb=256),
    assumptions=[
        "Input signal is a 1D float32 array with exactly 8 elements.",
        "All values are finite and within [-10, 10].",
        "This is a constructed demonstration case. See fixture/project/pipeline.py.",
    ],
    review_provenance="fixture_default_v1",
)
