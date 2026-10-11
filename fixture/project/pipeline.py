"""
Signal classification pipeline — constructed demonstration case.

This pipeline classifies a 1D float32 signal as 'above_threshold' or
'below_threshold' based on its normalized power spectral density ratio.

IMPORTANT: This is a constructed demonstration case to show a documented
NumPy 1.26→2.0 behavior difference. It is NOT an independently observed
research defect.

NumPy behavior that creates the difference:
- NumPy 1.26: float32 array * Python float scalar → float64 (upcasting)
- NumPy 2.0:  float32 array * Python float scalar → float32 (no upcasting)
This changes intermediate precision in the power computation, which can
flip the classification near the threshold.

Reference: https://numpy.org/doc/2.0/numpy_2_0_migration_guide.html
           (NEP 50 — scalar promotion)
"""
import numpy as np

THRESHOLD = 0.5  # Ratio threshold for classification


def compute_power_ratio(signal: np.ndarray) -> float:
    """
    Compute the ratio of high-frequency to total power.

    The signal must be a 1D float32 array with at least 4 elements.
    Returns a Python float (the ratio).

    The intermediate computation uses float32 arithmetic because the input
    is float32. Under NumPy 1.26, multiplying a float32 array by a Python
    float scalar upcasts to float64 (higher precision). Under NumPy 2.0
    with NEP 50, the result stays float32 (can differ near boundaries).
    """
    # Normalize: divide by a Python float scalar
    # NumPy 1.26: signal / 2.0 → float64 intermediate
    # NumPy 2.0:  signal / 2.0 → float32 intermediate (NEP 50)
    normalized = signal / 2.0

    # Compute element-wise squared power
    power = normalized ** 2

    # Split into two halves and sum each
    mid = len(power) // 2
    low_power = np.sum(power[:mid])
    high_power = np.sum(power[mid:])
    total_power = low_power + high_power

    if total_power == 0.0:
        return 0.0

    ratio = float(high_power / total_power)
    return ratio


def classify_signal(signal: np.ndarray) -> dict:
    """
    Classify a signal and return the result dict.

    Returns:
        {
          "ratio": float,
          "conclusion": "above_threshold" | "below_threshold",
          "threshold": float,
          "numpy_version": str,
          "intermediate_dtype": str,  # dtype of normalized array
        }
    """
    ratio = compute_power_ratio(signal)
    conclusion = "above_threshold" if ratio > THRESHOLD else "below_threshold"
    # Record what dtype the intermediate computation used
    normalized = signal / 2.0
    return {
        "ratio": ratio,
        "conclusion": conclusion,
        "threshold": THRESHOLD,
        "numpy_version": np.__version__,
        "intermediate_dtype": str(normalized.dtype),
    }
