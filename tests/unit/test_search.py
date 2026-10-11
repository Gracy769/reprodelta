"""
Unit tests for app/orchestrator/search.py.

Uses a stub runner so no real subprocess calls are made.
"""
from __future__ import annotations

import uuid

from app.domain.contract import FIXTURE_CONTRACT
from app.domain.types import BudgetConfig
from app.orchestrator.search import (
    SearchController,
    _sweep_candidates,
)
from app.providers.runner import RunManifest, RunnerAdapter, RunResult

# ---------------------------------------------------------------------------
# Helpers / stubs
# ---------------------------------------------------------------------------

VALID_SHA = "a" * 64


def _make_run_result(
    kind: str,
    conclusion: str = "above_threshold",
    ratio: float = 0.6,
    status: str = "ok",
) -> RunResult:
    outputs = (
        {
            "result": {
                "conclusion": conclusion,
                "ratio": ratio,
                "numpy_version": "1.26.4" if kind == "old" else "2.0.0",
                "intermediate_dtype": "float64" if kind == "old" else "float32",
            }
        }
        if status == "ok"
        else {}
    )
    return RunResult(
        run_id=f"test-{kind}-001",
        status=status,  # type: ignore[arg-type]
        exit_code=0 if status == "ok" else 1,
        outputs=outputs,
        stderr="",
    )


class _ConstantRunner(RunnerAdapter):
    """Always returns the same old/new pair regardless of input."""

    def __init__(self, old_conclusion: str = "above_threshold", new_conclusion: str = "above_threshold") -> None:
        self._old_conclusion = old_conclusion
        self._new_conclusion = new_conclusion

    def submit(self, manifest: RunManifest) -> RunResult:
        if manifest.kind == "old":
            return _make_run_result("old", self._old_conclusion)
        return _make_run_result("new", self._new_conclusion)


class _WitnessRunner(RunnerAdapter):
    """
    Returns the same conclusion for all inputs EXCEPT the known witness signal.
    For the witness signal it flips old→above / new→below.
    """

    WITNESS_SIGNAL = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0000001192092896]

    def submit(self, manifest: RunManifest) -> RunResult:
        sig = manifest.input_json.get("signal", [])
        # Check if the 8th element matches the witness ULP value
        is_witness = (
            len(sig) == 8
            and abs(float(sig[7]) - 1.0000001192092896) < 1e-12
        )
        if is_witness:
            conclusion = "above_threshold" if manifest.kind == "old" else "below_threshold"
        else:
            conclusion = "above_threshold"
        return _make_run_result(manifest.kind, conclusion)


class _ErrorRunner(RunnerAdapter):
    """Always returns an error status."""

    def submit(self, manifest: RunManifest) -> RunResult:
        return RunResult(
            run_id=manifest.run_id,
            status="error",
            exit_code=1,
            outputs={},
            stderr="simulated error",
        )


# ---------------------------------------------------------------------------
# Tests: sweep candidates
# ---------------------------------------------------------------------------

class TestSweepCandidates:
    def test_returns_list_of_dicts(self):
        candidates = _sweep_candidates()
        assert isinstance(candidates, list)
        assert all(isinstance(c, dict) for c in candidates)

    def test_all_have_signal_key(self):
        for c in _sweep_candidates():
            assert "signal" in c

    def test_includes_out_of_domain(self):
        """There must be at least one candidate that will fail validation."""
        from app.domain.validator import validate_input
        invalid = [
            c for c in _sweep_candidates()
            if not validate_input(FIXTURE_CONTRACT, c).valid
        ]
        assert len(invalid) >= 1

    def test_includes_sweep_near_ulp_boundary(self):
        """The sweep must include the known witness value."""
        import numpy as np
        witness_val = float(np.nextafter(np.float32(1.0), np.float32(2.0)))
        signals = [c["signal"] for c in _sweep_candidates()]
        found = any(
            len(s) == 8 and abs(float(s[7]) - witness_val) < 1e-12
            for s in signals
        )
        assert found, "Known witness signal not found in sweep candidates"


# ---------------------------------------------------------------------------
# Tests: SearchController
# ---------------------------------------------------------------------------

BUDGET = BudgetConfig(max_candidates=100, max_runs=200)


class TestSearchControllerFindsWitness:
    def test_finds_known_witness(self):
        runner = _WitnessRunner()
        ctrl = SearchController(FIXTURE_CONTRACT, BUDGET, runner)
        progress = ctrl.run_search(str(uuid.uuid4()))
        assert progress.status == "found_witness"
        assert progress.witness is not None

    def test_witness_input_matches_known_signal(self):
        runner = _WitnessRunner()
        ctrl = SearchController(FIXTURE_CONTRACT, BUDGET, runner)
        progress = ctrl.run_search(str(uuid.uuid4()))
        assert progress.witness is not None
        sig = progress.witness.input_json.get("signal", [])
        assert len(sig) == 8
        assert abs(float(sig[7]) - 1.0000001192092896) < 1e-12

    def test_witness_candidate_is_valid(self):
        runner = _WitnessRunner()
        ctrl = SearchController(FIXTURE_CONTRACT, BUDGET, runner)
        progress = ctrl.run_search(str(uuid.uuid4()))
        assert progress.witness is not None
        assert progress.witness.validity == "valid"

    def test_witness_pair_result_shows_conclusion_changed(self):
        runner = _WitnessRunner()
        ctrl = SearchController(FIXTURE_CONTRACT, BUDGET, runner)
        progress = ctrl.run_search(str(uuid.uuid4()))
        assert progress.witness is not None
        pr = progress.witness.pair_result
        assert pr is not None
        assert pr.comparison["conclusion_changed"] is True


class TestSearchControllerInvalidRejection:
    def test_invalid_candidates_rejected_before_running(self):
        """Invalid candidates must not be submitted to the runner."""
        submitted_inputs = []

        class _TrackingRunner(RunnerAdapter):
            def submit(self, manifest: RunManifest) -> RunResult:
                submitted_inputs.append(manifest.input_json)
                return _make_run_result(manifest.kind, "above_threshold")

        ctrl = SearchController(FIXTURE_CONTRACT, BUDGET, _TrackingRunner())
        ctrl.run_search(str(uuid.uuid4()))

        # The [15.0]*8 out-of-domain candidate must never appear in submitted inputs
        for inp in submitted_inputs:
            sig = inp.get("signal", [])
            assert not all(v == 15.0 for v in sig), "Out-of-domain candidate was submitted to runner"

    def test_invalid_count_incremented(self):
        runner = _ConstantRunner()
        ctrl = SearchController(FIXTURE_CONTRACT, BUDGET, runner)
        progress = ctrl.run_search(str(uuid.uuid4()))
        assert progress.invalid >= 1

    def test_invalid_record_has_rejection_reason(self):
        runner = _ConstantRunner()
        ctrl = SearchController(FIXTURE_CONTRACT, BUDGET, runner)
        progress = ctrl.run_search(str(uuid.uuid4()))
        invalid_recs = [c for c in progress.candidates if c.validity == "invalid"]
        assert len(invalid_recs) >= 1
        for rec in invalid_recs:
            assert rec.rejection_reason is not None


class TestSearchControllerBudgetExhaustion:
    def test_budget_exhaustion_stops_search(self):
        """With max_candidates=1, search must stop after 1 attempt."""
        tight_budget = BudgetConfig(max_candidates=1, max_runs=2)
        runner = _ConstantRunner()
        ctrl = SearchController(FIXTURE_CONTRACT, tight_budget, runner)
        progress = ctrl.run_search(str(uuid.uuid4()))
        assert progress.attempted <= 1
        assert progress.status in ("exhausted", "found_witness")

    def test_budget_remaining_decrements(self):
        runner = _ConstantRunner()
        ctrl = SearchController(FIXTURE_CONTRACT, BUDGET, runner)
        progress = ctrl.run_search(str(uuid.uuid4()))
        assert progress.budget_remaining == BUDGET.max_candidates - progress.attempted


class TestSearchControllerControls:
    def test_control_candidates_no_false_witness(self):
        """Controls (equal signals) must not produce conclusion changes."""
        runner = _ConstantRunner("above_threshold", "above_threshold")
        ctrl = SearchController(FIXTURE_CONTRACT, BUDGET, runner)
        progress = ctrl.run_search(str(uuid.uuid4()))
        # Controls are the first 3 valid candidates; none should show a change
        valid_before_witness = [
            c for c in progress.candidates
            if c.validity == "valid"
        ]
        for rec in valid_before_witness:
            if rec.pair_result is not None:
                pr = rec.pair_result
                changed = pr.comparison.get("conclusion_changed")
                # All controls must agree (no change)
                assert changed is False or changed is None, (
                    f"Control candidate unexpectedly showed conclusion change: {rec.input_json}"
                )
        # No witness when all same
        assert progress.witness is None


class TestSearchProgressCounts:
    def test_counts_are_consistent(self):
        runner = _WitnessRunner()
        ctrl = SearchController(FIXTURE_CONTRACT, BUDGET, runner)
        progress = ctrl.run_search(str(uuid.uuid4()))
        assert progress.attempted == len(progress.candidates)
        assert progress.valid + progress.invalid == progress.attempted

    def test_conclusion_changed_count(self):
        runner = _WitnessRunner()
        ctrl = SearchController(FIXTURE_CONTRACT, BUDGET, runner)
        progress = ctrl.run_search(str(uuid.uuid4()))
        # conclusion_changed should be at least 1 (the witness)
        assert progress.conclusion_changed >= 1
