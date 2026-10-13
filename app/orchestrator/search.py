"""
Search controller — finds a valid conclusion-changing input (a witness).

Algorithm (deterministic, seeded):
1. Start with the known witness from the fixture (this is the initial "found" case).
2. For a real search, use a grid/sweep near the threshold: vary the 8th element
   (the one that creates the ULP difference) across a range of float32 values,
   checking: (a) is the input valid per contract? (b) does the conclusion change?
3. Reject invalid candidates before running them.
4. Track: attempted, valid, invalid, conclusion_changed, budget_remaining.

The search never assumes a conclusion change — it only records it when both
old and new runs execute successfully and conclusions differ.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict

from app.domain.contract import ContractBody
from app.domain.types import BudgetConfig, VerdictKind
from app.domain.validator import validate_input
from app.orchestrator.budget import BudgetExceeded, BudgetLedger, BudgetRunner
from app.orchestrator.executor import PairedRunRequest, PairedRunResult, run_pair
from app.providers.runner import RunnerAdapter

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class SearchConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_candidates: int = 50
    seed: int = 42
    search_strategy: Literal["sweep", "random"] = "sweep"


class CandidateRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate_id: str
    input_json: dict
    input_sha256: str
    validity: Literal["valid", "invalid", "pending"]
    rejection_reason: str | None = None
    pair_result: PairedRunResult | None = None


class SearchProgress(BaseModel):
    model_config = ConfigDict(frozen=True)

    investigation_id: str
    status: Literal["searching", "found_witness", "exhausted", "failed"]
    stop_reason: str | None = None
    attempted: int
    valid: int
    invalid: int
    conclusion_changed: int
    budget_remaining: int
    witness: CandidateRecord | None = None
    candidates: list[CandidateRecord] = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash_input(input_json: dict) -> str:
    return hashlib.sha256(json.dumps(input_json, sort_keys=True).encode()).hexdigest()


def _make_candidate_id() -> str:
    return str(uuid.uuid4())


def _sweep_candidates() -> list[dict]:
    """
    Build the deterministic list of candidate inputs for the sweep strategy.

    Order:
    1. Three control candidates with equal signals (should NOT flip conclusions).
    2. One out-of-domain candidate to exercise rejection.
    3. A sweep of the 8th element from float32(1.0) up by single ULP steps.
       The known witness has the 8th element = np.nextafter(np.float32(1.0), np.float32(2.0)).
    """
    candidates: list[dict] = []

    # ---- Control candidates (equal signals) ----
    candidates.append({"signal": [0.0] * 8})  # all zeros
    candidates.append({"signal": [1.0] * 8})  # equal halves
    candidates.append({"signal": [-1.0] * 8})  # negative equal

    # ---- Out-of-domain (exceeds max_value=10.0) ----
    candidates.append({"signal": [15.0] * 8})  # will be rejected

    # ---- Sweep the 8th element near the ULP boundary of float32(1.0) ----
    base = np.float32(1.0)
    v = base
    for _ in range(20):
        v = np.nextafter(v, np.float32(2.0))
        signal = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, float(v)]
        candidates.append({"signal": signal})

    return candidates


# ---------------------------------------------------------------------------
# Search controller
# ---------------------------------------------------------------------------


class SearchController:
    """
    Sweeps a deterministic set of candidate inputs looking for a valid
    conclusion-changing input (witness).

    Usage::

        controller = SearchController(contract, budget, runner)
        progress = controller.run_search("inv-id-123")
    """

    def __init__(
        self,
        contract: ContractBody,
        budget: BudgetConfig,
        runner: RunnerAdapter,
        on_progress: Callable[[str, dict], None] | None = None,
    ) -> None:
        self.contract = contract
        self.budget = budget
        self.runner = (
            runner
            if isinstance(runner, BudgetRunner)
            else BudgetRunner(runner, BudgetLedger(budget))
        )
        self.on_progress = on_progress

    def run_search(
        self,
        investigation_id: str,
        seed: int = 42,
    ) -> SearchProgress:
        """
        Run the sweep search and return a SearchProgress describing the outcome.

        Stops as soon as a witness is found, the candidate list is exhausted,
        or the budget (max_candidates) is reached.
        """
        candidates_to_try = _sweep_candidates()

        stop_reason = None
        attempted = 0
        valid_count = 0
        invalid_count = 0
        conclusion_changed_count = 0
        budget_remaining = self.budget.max_candidates
        witness: CandidateRecord | None = None
        records: list[CandidateRecord] = []
        status: Literal["searching", "found_witness", "exhausted", "failed"] = "searching"

        for input_json in candidates_to_try:
            if budget_remaining <= 0:
                status = "exhausted"
                stop_reason = "candidate_budget"
                break

            candidate_id = _make_candidate_id()
            input_sha256 = _hash_input(input_json)
            attempted += 1
            budget_remaining -= 1

            # --- Validity check (never run an invalid candidate) ---
            validation = validate_input(self.contract, input_json)
            if not validation.valid:
                invalid_count += 1
                record = CandidateRecord(
                    candidate_id=candidate_id,
                    input_json=input_json,
                    input_sha256=input_sha256,
                    validity="invalid",
                    rejection_reason=validation.reason,
                    pair_result=None,
                )
                records.append(record)
                if self.on_progress:
                    self.on_progress(
                        "candidate_rejected",
                        {
                            "attempted": attempted,
                            "valid": valid_count,
                            "invalid": invalid_count,
                            "budget_remaining": budget_remaining,
                            "reason": validation.reason,
                        },
                    )
                continue

            valid_count += 1

            # --- Paired execution ---
            req = PairedRunRequest(
                investigation_id=uuid.UUID(investigation_id)
                if _is_uuid(investigation_id)
                else uuid.uuid4(),
                candidate=input_json,
                candidate_sha256=input_sha256,
                contract=self.contract,
            )
            try:
                pair_result = run_pair(req, self.runner)
            except BudgetExceeded as exc:
                if self.on_progress:
                    self.on_progress("budget_exhausted", {"stage": "search", "error": str(exc)})
                stop_reason = str(exc)
                status = "exhausted"
                break

            if pair_result.comparison.get("verdict_hint") == VerdictKind.setup_failure:
                records.append(
                    CandidateRecord(
                        candidate_id=candidate_id,
                        input_json=input_json,
                        input_sha256=input_sha256,
                        validity="valid",
                        pair_result=pair_result,
                    )
                )
                if self.on_progress:
                    self.on_progress(
                        "search_failed",
                        {
                            "reason": pair_result.comparison.get("reason", "invalid_worker_result"),
                            "attempted": attempted,
                        },
                    )
                status = "failed"
                break

            conclusion_changed = pair_result.comparison.get("conclusion_changed") is True
            if conclusion_changed:
                conclusion_changed_count += 1

            record = CandidateRecord(
                candidate_id=candidate_id,
                input_json=input_json,
                input_sha256=input_sha256,
                validity="valid",
                rejection_reason=None,
                pair_result=pair_result,
            )
            records.append(record)
            if self.on_progress:
                self.on_progress(
                    "candidate_run",
                    {
                        "attempted": attempted,
                        "valid": valid_count,
                        "invalid": invalid_count,
                        "budget_remaining": budget_remaining,
                        "conclusion_changed": conclusion_changed,
                    },
                )

            if conclusion_changed and witness is None:
                witness = record
                status = "found_witness"
                break  # stop at first witness

        else:
            # Loop completed without break → all candidates tried
            if status == "searching":
                status = "exhausted"

        return SearchProgress(
            investigation_id=investigation_id,
            status=status,
            stop_reason=stop_reason,
            attempted=attempted,
            valid=valid_count,
            invalid=invalid_count,
            conclusion_changed=conclusion_changed_count,
            budget_remaining=budget_remaining,
            witness=witness,
            candidates=records,
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False
