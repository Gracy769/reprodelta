"""Compatibility replay of NumPy's independently documented lstsq default change.

Source: https://github.com/numpy/numpy/pull/25721 (NumPy 2.0).
The bounded matrix is ours, not a claim of a downstream scientific defect.
"""
import numpy as np


def classify_rank(matrix):
    matrix = np.asarray(matrix, dtype=np.float64)
    coefficients, residuals, rank, singular_values = np.linalg.lstsq(matrix, [1., 1., 0., 0.])
    ratio = float(rank / 2)
    return {"ratio": ratio, "conclusion": "above_threshold" if ratio > .75 else "below_threshold",
            "threshold": .75, "rank": int(rank), "coefficients": coefficients.tolist(),
            "singular_values": singular_values.tolist(), "residuals": residuals.tolist(),
            "numpy_version": np.__version__, "intermediate_dtype": str(matrix.dtype)}
