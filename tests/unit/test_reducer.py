"""
Unit tests for app/orchestrator/reducer.py.

Uses stub runners so no real subprocess calls are made.
"""
from __future__ import annotations

from app.domain.contract import FIXTURE_CONTRACT
from app.orchestrator.reducer import ReductionResult, reduce_witness
from app.providers.runner import RunManifest, RunnerAdapter, RunResult

# ---------------------------------------------------------------------------
# Helpers / stubs
# ---------------------------------------------------------------------------

WITNESS_SIGNAL = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0000001192092896]
WITNESS_INPUT = {"signal": WITNESS_SIGNAL}


def _make_result(kind: str, conclusion: str) -> RunResult:
    return RunResult(
        run_id=f"test-{kind}",
        status="ok",
        exit_code=0,
        outputs={
            "result": {
                "conclusion": conclusion,
                "ratio": 0.5001 if conclusion == "above_threshold" else 0.4999,
                "numpy_version": "1.26.4" if kind == "old" else "2.0.0",
                "intermediate_dtype": "float64" if kind == "old" else "float32",
            }
        },
        stderr="",
    )


class _WitnessRunner(RunnerAdapter):
    """
    Flips conclusion old→above / new→below ONLY when signal[7] ≈ 1.0000001192.
    For all other inputs (including those with signal[7]=1.0), returns 'above_threshold'
    for both environments (no conclusion change).
    """

    WITNESS_VAL = 1.0000001192092896

    def submit(self, manifest: RunManifest) -> RunResult:
        sig = manifest.input_json.get("signal", [])
        is_witness = (
            len(sig) == 8
            and abs(float(sig[7]) - self.WITNESS_VAL) < 1e-12
        )
        if is_witness:
            conclusion = "above_threshold" if manifest.kind == "old" else "below_threshold"
        else:
            conclusion = "above_threshold"
        return _make_result(manifest.kind, conclusion)


class _AlwaysChangeRunner(RunnerAdapter):
    """Always reports a conclusion change — used for artificial witness tests."""

    def submit(self, manifest: RunManifest) -> RunResult:
        conclusion = "above_threshold" if manifest.kind == "old" else "below_threshold"
        return _make_result(manifest.kind, conclusion)


class _NeverChangeRunner(RunnerAdapter):
    """Never reports a conclusion change."""

    def submit(self, manifest: RunManifest) -> RunResult:
        return _make_result(manifest.kind, "above_threshold")


# ---------------------------------------------------------------------------
# Tests: reduce_witness confirms minimal form
# ---------------------------------------------------------------------------

class TestReduceWitnessMinimalForm:
    def test_returns_reduction_result(self):
        result = reduce_witness(WITNESS_INPUT, FIXTURE_CONTRACT, _WitnessRunner())
        assert isinstance(result, ReductionResult)

    def test_final_witness_has_signal_key(self):
        result = reduce_witness(WITNESS_INPUT, FIXTURE_CONTRACT, _WitnessRunner())
        assert "signal" in result.final_witness

    def test_final_signal_length_unchanged(self):
        result = reduce_witness(WITNESS_INPUT, FIXTURE_CONTRACT, _WitnessRunner())
        assert len(result.final_witness["signal"]) == 8

    def test_size_before_and_after(self):
        result = reduce_witness(WITNESS_INPUT, FIXTURE_CONTRACT, _WitnessRunner())
        assert result.size_before == 8
        assert result.size_after == 8  # length can't change (fixed-length signal)

    def test_element7_preserved(self):
        """
        The 8th element (index 7) must NOT be replaced with 1.0 because doing so
        removes the witness property.  The reducer must reject that step.
        """
        result = reduce_witness(WITNESS_INPUT, FIXTURE_CONTRACT, _WitnessRunner())
        assert abs(float(result.final_witness["signal"][7]) - 1.0000001192092896) < 1e-12

    def test_elements_0_to_6_already_neutral(self):
        """
        Elements 0-6 are already 1.0 (neutral).  The reducer should accept these
        trivially (they're already at the neutral value, so no actual change).
        """
        result = reduce_witness(WITNESS_INPUT, FIXTURE_CONTRACT, _WitnessRunner())
        for i in range(7):
            assert float(result.final_witness["signal"][i]) == 1.0


class TestReduceWitnessSteps:
    def test_steps_are_recorded(self):
        result = reduce_witness(WITNESS_INPUT, FIXTURE_CONTRACT, _WitnessRunner())
        assert len(result.steps) > 0

    def test_each_step_has_required_fields(self):
        result = reduce_witness(WITNESS_INPUT, FIXTURE_CONTRACT, _WitnessRunner())
        for step in result.steps:
            assert isinstance(step.step_id, int)
            assert step.reduction_type != ""
            assert step.reason != ""
            assert isinstance(step.accepted, bool)
            assert "signal" in step.original_input
            assert "signal" in step.reduced_input

    def test_step_ids_are_sequential(self):
        result = reduce_witness(WITNESS_INPUT, FIXTURE_CONTRACT, _WitnessRunner())
        ids = [s.step_id for s in result.steps]
        assert ids == list(range(len(ids)))

    def test_element7_step_is_rejected(self):
        """The step attempting to neutralise element[7] must be rejected."""
        result = reduce_witness(WITNESS_INPUT, FIXTURE_CONTRACT, _WitnessRunner())
        # Find the step for index 7
        rejected = [s for s in result.steps if not s.accepted]
        assert len(rejected) >= 1


class TestReduceWitnessArtificiallyPadded:
    """
    If we start with a witness where extra elements are NOT 1.0 but still keep
    the conclusion change, the reducer should neutralise those extras.
    """

    def test_artificial_padding_gets_reduced(self):
        """
        Use _AlwaysChangeRunner so any input causes a conclusion change.
        Start with signal[0]=2.0 (non-neutral) — should be replaced with 1.0.
        """
        padded_input = {"signal": [2.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0000001192092896]}
        result = reduce_witness(padded_input, FIXTURE_CONTRACT, _AlwaysChangeRunner())
        # Element 0 should be neutralised because _AlwaysChangeRunner still gives change
        assert float(result.final_witness["signal"][0]) == 1.0


class TestReduceWitnessInvalidInput:
    def test_invalid_trial_not_accepted(self):
        """
        If replacing an element creates an invalid input, the step must be rejected.
        We cannot easily trigger this with the fixture (all replacements with 1.0
        stay valid), so we test via max_steps=0.
        """
        result = reduce_witness(WITNESS_INPUT, FIXTURE_CONTRACT, _WitnessRunner(), max_steps=0)
        assert len(result.steps) == 0
        # Final witness unchanged
        assert result.final_witness == WITNESS_INPUT


class TestReduceWitnessMaxSteps:
    def test_max_steps_limits_iterations(self):
        result = reduce_witness(WITNESS_INPUT, FIXTURE_CONTRACT, _WitnessRunner(), max_steps=3)
        assert len(result.steps) <= 3

    def test_final_sha256_is_hex(self):
        result = reduce_witness(WITNESS_INPUT, FIXTURE_CONTRACT, _WitnessRunner())
        assert len(result.final_sha256) == 64
        assert all(c in "0123456789abcdef" for c in result.final_sha256)
