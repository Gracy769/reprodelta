"""Unit tests for the fixture pipeline (Slice 2)."""
from __future__ import annotations

import numpy as np
import pytest

from fixture.find_witness import (
    compute_ratio_float32,
    compute_ratio_float64,
    search_witness,
)
from fixture.project.pipeline import THRESHOLD, classify_signal, compute_power_ratio


class TestComputePowerRatio:
    def test_equal_halves_returns_half(self):
        # When both halves are identical, ratio must be exactly 0.5
        signal = np.ones(8, dtype=np.float32)
        ratio = compute_power_ratio(signal)
        assert ratio == pytest.approx(0.5)

    def test_all_zeros_returns_zero(self):
        signal = np.zeros(8, dtype=np.float32)
        assert compute_power_ratio(signal) == 0.0

    def test_high_dominant(self):
        signal = np.array([0.1, 0.1, 0.1, 0.1, 1.0, 1.0, 1.0, 1.0], dtype=np.float32)
        assert compute_power_ratio(signal) > THRESHOLD

    def test_low_dominant(self):
        signal = np.array([1.0, 1.0, 1.0, 1.0, 0.1, 0.1, 0.1, 0.1], dtype=np.float32)
        assert compute_power_ratio(signal) < THRESHOLD


class TestClassifySignal:
    def test_returns_required_keys(self):
        signal = np.ones(8, dtype=np.float32)
        result = classify_signal(signal)
        for key in ("ratio", "conclusion", "threshold", "numpy_version", "intermediate_dtype"):
            assert key in result, f"Missing key: {key!r}"

    def test_numpy_version_matches(self):
        signal = np.ones(8, dtype=np.float32)
        result = classify_signal(signal)
        assert result["numpy_version"] == np.__version__

    def test_threshold_value(self):
        signal = np.ones(8, dtype=np.float32)
        result = classify_signal(signal)
        assert result["threshold"] == THRESHOLD

    def test_conclusion_values(self):
        for arr, expected in [
            (np.array([0.1, 0.1, 0.1, 0.1, 1.0, 1.0, 1.0, 1.0], dtype=np.float32), "above_threshold"),
            (np.array([1.0, 1.0, 1.0, 1.0, 0.1, 0.1, 0.1, 0.1], dtype=np.float32), "below_threshold"),
        ]:
            assert classify_signal(arr)["conclusion"] == expected


class TestSimulatedBehaviorDifference:
    def test_known_witness_produces_different_ratios(self):
        """The found witness must give different float64 vs float32 ratios."""
        witness = search_witness()
        assert witness is not None, "search_witness() returned None — no witness found"
        signal = np.array(witness["signal"], dtype=np.float32)
        r64 = compute_ratio_float64(signal)
        r32 = compute_ratio_float32(signal)
        assert r64 != r32, "float64 and float32 ratios must differ for the witness"

    def test_known_witness_produces_different_conclusions(self):
        """The found witness must flip the classification conclusion."""
        witness = search_witness()
        assert witness is not None
        assert witness["conclusion_numpy126"] != witness["conclusion_numpy20"], (
            "Witness conclusions must differ between simulated NumPy 1.26 and 2.0 behaviors"
        )

    def test_float64_simulation_uses_float64(self):
        signal = np.ones(4, dtype=np.float32)
        # Cast to float64 — the sum result should be a Python float or float64
        signal_f64 = signal.astype(np.float64)
        normalized = signal_f64 / 2.0
        assert normalized.dtype == np.float64

    def test_float32_simulation_stays_float32(self):
        signal = np.ones(4, dtype=np.float32)
        normalized = np.divide(signal, np.float32(2.0))
        assert normalized.dtype == np.float32
