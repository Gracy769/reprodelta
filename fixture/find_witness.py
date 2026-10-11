"""
Empirically find a float32 input signal where NumPy 1.26 and NumPy 2.0
would produce different conclusions (different intermediate dtypes).

This simulates the two behaviors without needing two separate numpy installs.
NumPy 1.26 behavior: float32 scalar / 2.0 -> float64
NumPy 2.0 behavior:  float32 scalar / 2.0 -> float32 (NEP 50)

We search for a signal where:
  - compute_ratio_float64(signal) > 0.5  (old behavior conclusion: above_threshold)
  - compute_ratio_float32(signal) <= 0.5 (new behavior conclusion: below_threshold)
  OR vice versa.

Strategy: use np.nextafter to enumerate float32 values adjacent to 1.0 and find
a signal whose power halves straddle 0.5 differently under the two dtype regimes.
A random fallback with seed=42 is also included.
"""
import json

import numpy as np

THRESHOLD = 0.5


def compute_ratio_float64(signal_f32):
    """Simulate NumPy 1.26 behavior: intermediates promoted to float64."""
    signal_f64 = signal_f32.astype(np.float64)
    normalized = signal_f64 / 2.0  # float64 intermediate
    power = normalized ** 2
    mid = len(power) // 2
    low_power = np.sum(power[:mid])
    high_power = np.sum(power[mid:])
    total_power = low_power + high_power
    if total_power == 0.0:
        return 0.0
    return float(high_power / total_power)


def compute_ratio_float32(signal_f32):
    """Simulate NumPy 2.0 behavior: intermediates stay float32."""
    normalized = np.divide(signal_f32, np.float32(2.0))  # stay float32
    power = normalized ** 2
    mid = len(power) // 2
    low_power = np.sum(power[:mid])
    high_power = np.sum(power[mid:])
    total_power = low_power + high_power
    if total_power == 0.0:
        return 0.0
    return float(high_power / total_power)


def _check(signal):
    """Return a witness dict if this signal flips conclusions, else None."""
    r64 = compute_ratio_float64(signal)
    r32 = compute_ratio_float32(signal)
    c64 = "above_threshold" if r64 > THRESHOLD else "below_threshold"
    c32 = "above_threshold" if r32 > THRESHOLD else "below_threshold"
    if c64 != c32:
        return {
            "signal": signal.tolist(),
            "ratio_float64": r64,
            "ratio_float32": r32,
            "conclusion_numpy126": c64,
            "conclusion_numpy20": c32,
        }
    return None


def search_witness():
    """
    Find a float32 signal that produces different conclusions under the two
    simulated NumPy behaviors.

    First tries an analytical sweep near float32 boundaries (fast, deterministic).
    Falls back to a random search with seed=42 if the sweep misses.

    Returns a dict with the witness details, or None if nothing is found.
    """
    # --- Analytical fast path ---------------------------------------------------
    # Use low=[1,1,1,1] and vary the last element of high with nextafter steps.
    # This reliably finds a flip at the float32 ULP boundary near 1.0.
    base = np.float32(1.0)
    for direction in (np.float32(2.0), np.float32(0.0)):
        v = base
        for _ in range(16):
            v = np.nextafter(v, direction)
            for low_vals in (
                [base, base, base, base],
                [v, v, v, v],
            ):
                for high_vals in (
                    [base, base, base, v],
                    [v, base, base, base],
                    [base, v, base, base],
                ):
                    signal = np.array(low_vals + high_vals, dtype=np.float32)
                    w = _check(signal)
                    if w is not None:
                        w["attempts"] = 1
                        return w

    # --- Random fallback (seed=42) ----------------------------------------------
    rng = np.random.default_rng(42)
    for attempt in range(1, 200_001):
        low = rng.uniform(0.5, 1.5, size=4).astype(np.float32)
        high = rng.uniform(0.5, 1.5, size=4).astype(np.float32)
        signal = np.concatenate([low, high])
        w = _check(signal)
        if w is not None:
            w["attempts"] = attempt
            return w

    return None


if __name__ == "__main__":
    result = search_witness()
    if result:
        print("WITNESS FOUND:")
        print(json.dumps(result, indent=2))
    else:
        print("No witness found in search budget")
