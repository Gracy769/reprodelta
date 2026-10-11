"""
Structured evidence bundle for an investigation.

These types capture all evidence gathered during an investigation run and
are used as the pre-export, in-memory representation before rendering or
serialising to JSON.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from app.domain.contract import ContractBody
from app.domain.types import VerdictKind

if TYPE_CHECKING:
    pass


class RunEvidence(BaseModel):
    """Evidence captured from a single run."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    environment: str
    numpy_version: str | None
    conclusion: str | None
    ratio: float | None
    intermediate_dtype: str | None
    status: str


class WitnessEvidence(BaseModel):
    """Evidence for the conclusion-changing input (the witness)."""

    model_config = ConfigDict(frozen=True)

    input_json: dict
    input_sha256: str
    old_run: RunEvidence
    new_run: RunEvidence
    conclusion_changed: bool
    verdict: VerdictKind


class ControlEvidence(BaseModel):
    """Evidence for a repeatability control run (same env, same input, twice)."""

    model_config = ConfigDict(frozen=True)

    input_json: dict
    input_sha256: str
    run1: RunEvidence
    run2: RunEvidence
    repeatability_confirmed: bool


class InvestigationEvidence(BaseModel):
    """Complete evidence bundle for a finished investigation."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    investigation_id: str
    project_name: str
    contract_hash: str
    contract_body: ContractBody
    controls: list[ControlEvidence]
    witness: WitnessEvidence | None
    # ReductionResult is imported lazily to avoid a circular import chain;
    # we type it as Any at runtime but annotate with the real type for IDEs.
    reduction: object | None  # actually ReductionResult | None
    verdict: VerdictKind
    scope: str
    timestamp: datetime
    # Slice 5: model proposals, intervention, and patch verification results
    model_proposals: list[object] = []   # list[ModelProposal]
    intervention: object | None = None   # InterventionRunResult | None
    patch_results: list[object] = []     # list[VerificationResult]
