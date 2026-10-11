"""
Integration test: verify the known conclusion-flipping witness.

This is the "real conclusion-changing witness" test required by the PRD.
It confirms that the constructed demonstration case produces a measurably
different classification result depending on whether float64 or float32
intermediate arithmetic is used (simulating NumPy 1.26 vs NumPy 2.0).
"""
from __future__ import annotations

import numpy as np
import pytest

from fixture.find_witness import (
    THRESHOLD,
    compute_ratio_float32,
    compute_ratio_float64,
    search_witness,
)


@pytest.fixture(scope="module")
def witness():
    """Find the witness once for all tests in this module."""
    result = search_witness()
    assert result is not None, (
        "search_witness() returned None — no witness found within 100 000 attempts. "
        "The constructed demonstration case is broken."
    )
    return result


class TestKnownWitness:
    def test_witness_signal_is_float32(self, witness):
        signal = np.array(witness["signal"], dtype=np.float32)
        assert signal.dtype == np.float32

    def test_float64_ratio_near_threshold(self, witness):
        """The float64 ratio must be close enough to 0.5 to be interesting."""
        r64 = witness["ratio_float64"]
        assert abs(r64 - THRESHOLD) < 0.01, (
            f"float64 ratio {r64} is too far from threshold {THRESHOLD}"
        )

    def test_float32_ratio_near_threshold(self, witness):
        """The float32 ratio must also be close to 0.5."""
        r32 = witness["ratio_float32"]
        assert abs(r32 - THRESHOLD) < 0.01, (
            f"float32 ratio {r32} is too far from threshold {THRESHOLD}"
        )

    def test_ratios_differ(self, witness):
        """The two simulated environments must produce numerically different ratios."""
        assert witness["ratio_float64"] != witness["ratio_float32"], (
            "float64 and float32 ratios are identical — witness is not valid"
        )

    def test_conclusions_differ(self, witness):
        """This is the core assertion: the classification flips between environments."""
        assert witness["conclusion_numpy126"] != witness["conclusion_numpy20"], (
            "Conclusions are the same — the witness does not demonstrate a behavior difference"
        )

    def test_conclusion_values_are_valid(self, witness):
        valid = {"above_threshold", "below_threshold"}
        assert witness["conclusion_numpy126"] in valid
        assert witness["conclusion_numpy20"] in valid

    def test_witness_is_reproducible(self, witness):
        """Re-running search_witness with the same seed must return the same signal."""
        second = search_witness()
        assert second is not None
        assert second["signal"] == witness["signal"], (
            "search_witness() is not reproducible — check that rng seed=42 is used"
        )

    def test_manual_recompute_matches(self, witness):
        """Manually recompute ratios and check they match the stored values."""
        signal = np.array(witness["signal"], dtype=np.float32)
        r64 = compute_ratio_float64(signal)
        r32 = compute_ratio_float32(signal)
        assert r64 == pytest.approx(witness["ratio_float64"])
        assert r32 == pytest.approx(witness["ratio_float32"])
