"""Core domain types for ReproDelta."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, field_validator


class InvestigationStatus(StrEnum):
    """Lifecycle states for an investigation."""

    draft = "draft"
    preflight = "preflight"
    approved = "approved"
    running = "running"
    verifying = "verifying"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class VerdictKind(StrEnum):
    """Bounded verdict that can be issued at the end of an investigation.

    Exactly seven values per PRD — never add more without updating the contract schema.
    """

    observed_drift = "observed_drift"
    supported_conclusion_change = "supported_conclusion_change"
    pre_existing_contract_violation = "pre_existing_contract_violation"
    repair_passes_stated_checks = "repair_passes_stated_checks"
    no_violation_found = "no_violation_found"
    inconclusive = "inconclusive"
    setup_failure = "setup_failure"


class BudgetConfig(BaseModel):
    """Execution budget attached to an investigation."""

    max_candidates: Annotated[int, Field(gt=0, le=10_000)] = 500
    max_runs: Annotated[int, Field(gt=0, le=50_000)] = 2000
    max_wall_seconds: Annotated[int, Field(gt=0, le=86_400)] = 3600
    max_llm_calls: Annotated[int, Field(gt=0, le=1_000)] = 50
    max_cost_usd: Annotated[float, Field(gt=0.0, le=1_000.0)] = 10.0

    @field_validator("max_runs")
    @classmethod
    def _runs_gte_candidates(cls, v: int, info) -> int:
        candidates = info.data.get("max_candidates")
        if candidates is not None and v < candidates:
            raise ValueError("max_runs must be >= max_candidates")
        return v


class ArtifactRef(BaseModel):
    """Lightweight reference to content-addressed artifact storage."""

    sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    byte_size: int = Field(ge=0)
    media_type: str = Field(default="application/octet-stream")
    storage_key: str  # relative path inside ARTIFACT_DIR

    @classmethod
    def from_hash(cls, sha256: str, byte_size: int, media_type: str = "application/octet-stream") -> ArtifactRef:
        storage_key = f"{sha256[:2]}/{sha256}"
        return cls(sha256=sha256, byte_size=byte_size, media_type=media_type, storage_key=storage_key)
