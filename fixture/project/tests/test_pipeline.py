import numpy as np

from fixture.project.pipeline import classify_signal


def test_all_zeros():
    signal = np.zeros(8, dtype=np.float32)
    result = classify_signal(signal)
    assert result["conclusion"] in ("above_threshold", "below_threshold")
    assert result["ratio"] == 0.0


def test_high_frequency_dominant():
    # Second half much larger than first half → ratio > 0.5
    signal = np.array([0.1, 0.1, 0.1, 0.1, 1.0, 1.0, 1.0, 1.0], dtype=np.float32)
    result = classify_signal(signal)
    assert result["conclusion"] == "above_threshold"


def test_low_frequency_dominant():
    # First half much larger → ratio < 0.5
    signal = np.array([1.0, 1.0, 1.0, 1.0, 0.1, 0.1, 0.1, 0.1], dtype=np.float32)
    result = classify_signal(signal)
    assert result["conclusion"] == "below_threshold"


def test_returns_numpy_version():
    signal = np.ones(8, dtype=np.float32)
    result = classify_signal(signal)
    assert "numpy_version" in result
    assert result["numpy_version"] == np.__version__
