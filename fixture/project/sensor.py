"""Constructed sensor calibration case exposing scalar addition precision.

This is an independently executed expression, not a historical research defect.
The calibrated first reading is compared to a fixed decision boundary of 1.0.
"""
import numpy as np


def classify_sensor(signal: np.ndarray) -> dict:
    calibrated = signal[0] + 1e-8
    ratio = float(calibrated)
    return {
        "ratio": ratio,
        "conclusion": "above_threshold" if ratio > 1.0 else "below_threshold",
        "threshold": 1.0,
        "numpy_version": np.__version__,
        "intermediate_dtype": str(calibrated.dtype),
    }
