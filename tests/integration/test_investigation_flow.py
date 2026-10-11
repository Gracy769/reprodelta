"""
Integration test for the full investigation flow.

Uses LocalRunnerAdapter with the real fixture project — proves the end-to-end
investigation (controls → search → reduction → evidence) works.

These tests are skipped automatically when the fixture directory is absent
(e.g. in a stripped CI environment).
"""
from __future__ import annotations

import pathlib
import uuid

import pytest

from app.domain.contract import FIXTURE_CONTRACT
from app.domain.evidence import InvestigationEvidence
from app.domain.types import BudgetConfig, VerdictKind
from app.orchestrator.investigation import InvestigationRunner
from app.providers.runner import LocalRunnerAdapter

# ---------------------------------------------------------------------------
# Fixture directory detection
# ---------------------------------------------------------------------------

_HERE = pathlib.Path(__file__).parent.parent.parent  # repo root
_FIXTURE_DIR = _HERE / "fixture" / "project"

_FIXTURE_AVAILABLE = _FIXTURE_DIR.exists()

pytestmark = pytest.mark.skipif(
    not _FIXTURE_AVAILABLE,
    reason="Fixture directory not available in this environment",
)


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def local_runner() -> LocalRunnerAdapter:
    return LocalRunnerAdapter(fixture_dir=_FIXTURE_DIR)


@pytest.fixture(scope="module")
def investigation_evidence(local_runner: LocalRunnerAdapter) -> InvestigationEvidence:
    """Run the full investigation once and share the result across tests."""
    budget = BudgetConfig(max_candidates=100, max_runs=200)
    inv_runner = InvestigationRunner(
        contract=FIXTURE_CONTRACT,
        budget=budget,
        runner=local_runner,
    )
    return inv_runner.run_full_investigation(str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# Tests: basic structure
# ---------------------------------------------------------------------------

class TestInvestigationEvidenceStructure:
    def test_returns_investigation_evidence(self, investigation_evidence):
        assert isinstance(investigation_evidence, InvestigationEvidence)

    def test_has_investigation_id(self, investigation_evidence):
        assert investigation_evidence.investigation_id != ""

    def test_has_contract_hash(self, investigation_evidence):
        assert len(investigation_evidence.contract_hash) == 64

    def test_has_timestamp(self, investigation_evidence):
        from datetime import datetime
        assert isinstance(investigation_evidence.timestamp, datetime)


# ---------------------------------------------------------------------------
# Tests: controls are repeatable
# ---------------------------------------------------------------------------

class TestControls:
    def test_controls_present(self, investigation_evidence):
        assert len(investigation_evidence.controls) == 2

    def test_old_control_repeatable(self, investigation_evidence):
        old_ctrl = next(
            c for c in investigation_evidence.controls if c.run1.environment == "old"
        )
        assert old_ctrl.repeatability_confirmed is True, (
            f"Old control not repeatable: run1={old_ctrl.run1.conclusion}, "
            f"run2={old_ctrl.run2.conclusion}"
        )

    def test_new_control_repeatable(self, investigation_evidence):
        new_ctrl = next(
            c for c in investigation_evidence.controls if c.run1.environment == "new"
        )
        assert new_ctrl.repeatability_confirmed is True, (
            f"New control not repeatable: run1={new_ctrl.run1.conclusion}, "
            f"run2={new_ctrl.run2.conclusion}"
        )

    def test_controls_both_succeed(self, investigation_evidence):
        for ctrl in investigation_evidence.controls:
            assert ctrl.run1.status == "ok"
            assert ctrl.run2.status == "ok"


# ---------------------------------------------------------------------------
# Tests: witness found
# ---------------------------------------------------------------------------

class TestWitness:
    def test_witness_found(self, investigation_evidence):
        assert investigation_evidence.witness is not None, (
            "No witness found — the search did not find the known conclusion-changing input"
        )

    def test_witness_conclusion_changed(self, investigation_evidence):
        assert investigation_evidence.witness is not None
        assert investigation_evidence.witness.conclusion_changed is True

    def test_witness_old_run_ok(self, investigation_evidence):
        assert investigation_evidence.witness is not None
        assert investigation_evidence.witness.old_run.status == "ok"

    def test_witness_new_run_ok(self, investigation_evidence):
        assert investigation_evidence.witness is not None
        assert investigation_evidence.witness.new_run.status == "ok"

    def test_witness_conclusions_differ(self, investigation_evidence):
        assert investigation_evidence.witness is not None
        w = investigation_evidence.witness
        assert w.old_run.conclusion != w.new_run.conclusion, (
            f"Expected different conclusions, got old={w.old_run.conclusion} "
            f"new={w.new_run.conclusion}"
        )

    def test_witness_signal_is_valid(self, investigation_evidence):
        """The witness input must pass the contract's validity check."""
        from app.domain.validator import validate_input
        assert investigation_evidence.witness is not None
        result = validate_input(FIXTURE_CONTRACT, investigation_evidence.witness.input_json)
        assert result.valid, f"Witness input is invalid: {result.reason}"

    def test_witness_signal_has_ulp_element(self, investigation_evidence):
        """The known witness has signal[7] ≈ 1.0000001192092896."""
        assert investigation_evidence.witness is not None
        sig = investigation_evidence.witness.input_json.get("signal", [])
        assert len(sig) == 8
        assert abs(float(sig[7]) - 1.0000001192092896) < 1e-12, (
            f"Unexpected witness signal[7]: {sig[7]}"
        )


# ---------------------------------------------------------------------------
# Tests: verdict
# ---------------------------------------------------------------------------

class TestVerdict:
    def test_verdict_is_supported_conclusion_change(self, investigation_evidence):
        assert investigation_evidence.verdict == VerdictKind.supported_conclusion_change


# ---------------------------------------------------------------------------
# Tests: reduction
# ---------------------------------------------------------------------------

class TestReduction:
    def test_reduction_present(self, investigation_evidence):
        assert investigation_evidence.reduction is not None

    def test_reduction_final_witness_matches_witness(self, investigation_evidence):
        """The reduced final witness must preserve the original witness signal."""
        assert investigation_evidence.reduction is not None
        assert investigation_evidence.witness is not None
        final = investigation_evidence.reduction.final_witness
        original = investigation_evidence.witness.input_json
        # Both must have the same 8th element (the ULP witness element)
        assert abs(float(final["signal"][7]) - float(original["signal"][7])) < 1e-12

    def test_reduction_steps_recorded(self, investigation_evidence):
        assert investigation_evidence.reduction is not None
        assert len(investigation_evidence.reduction.steps) > 0

    def test_reduction_already_minimal(self, investigation_evidence):
        """
        The known witness is already minimal: only element 7 differs from 1.0.
        The reducer must confirm this by rejecting exactly one step.
        """
        assert investigation_evidence.reduction is not None
        rejected = [s for s in investigation_evidence.reduction.steps if not s.accepted]
        assert len(rejected) == 1, (
            f"Expected exactly 1 rejected step (element 7), got {len(rejected)}"
        )

    def test_final_sha256_format(self, investigation_evidence):
        assert investigation_evidence.reduction is not None
        sha = investigation_evidence.reduction.final_sha256
        assert len(sha) == 64
        assert all(c in "0123456789abcdef" for c in sha)


# ---------------------------------------------------------------------------
# Tests: serialisation
# ---------------------------------------------------------------------------

class TestSerialisation:
    def test_model_dump_does_not_raise(self, investigation_evidence):
        data = investigation_evidence.model_dump(mode="json")
        assert isinstance(data, dict)

    def test_serialised_has_expected_keys(self, investigation_evidence):
        data = investigation_evidence.model_dump(mode="json")
        for key in ("investigation_id", "verdict", "controls", "witness", "reduction"):
            assert key in data, f"Missing key in serialised evidence: {key}"
