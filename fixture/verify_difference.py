"""
Integration verification script.
Confirms that the NumPy 1.26 vs 2.0 behavior difference is real and
empirically observable, by simulating both behaviors in the current environment.

Run with: python fixture/verify_difference.py

This does NOT require two separate NumPy installs — it simulates the
precision difference by using explicit dtype-preserving and dtype-promoting
arithmetic to model each environment's behavior.

NumPy 1.26 behavior: float32 scalar / Python float → float64 intermediate (upcasting)
NumPy 2.0 behavior:  float32 scalar / Python float → float32 intermediate (NEP 50)
"""
import json
import sys

import numpy as np

# ---------------------------------------------------------------------------
# Import the search logic from find_witness.
# Works both when run as a script directly (sys.path insert) and as a module.
# ---------------------------------------------------------------------------
try:
    from fixture.find_witness import (
        THRESHOLD,
        compute_ratio_float32,
        compute_ratio_float64,
        search_witness,
    )
except ModuleNotFoundError:
    # Running as a standalone script; add repo root to path
    import os
    _repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)
    from fixture.find_witness import (
        THRESHOLD,
        compute_ratio_float32,
        compute_ratio_float64,
        search_witness,
    )


def _section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print('=' * 60)


def main() -> int:
    _section("Environment")
    print(f"  Python     : {sys.version.split()[0]}")
    print(f"  NumPy      : {np.__version__}")

    _section("Precision difference demonstration")
    # Show the raw division behavior on this NumPy version
    x = np.float32(1.0)
    result_div = x / 2.0
    print(f"  np.float32(1.0) / 2.0  ->  dtype={result_div.dtype}, value={result_div}")
    if result_div.dtype == np.float64:
        print("  -> Matches NumPy 1.26 behavior (float64 upcast, NEP 50 inactive)")
    else:
        print("  -> Matches NumPy 2.0 behavior (float32 preserved, NEP 50 active)")

    _section("Searching for a conclusion-flipping witness (seed=42)")
    print("  Simulating BOTH behaviors regardless of installed NumPy version.")
    print("  NumPy 1.26 simulated via: signal.astype(float64) / 2.0")
    print("  NumPy 2.0  simulated via: np.divide(signal_f32, np.float32(2.0))\n")

    witness = search_witness()

    if witness is None:
        print("ERROR: No witness found within the search budget.")
        return 1

    _section("Witness found!")
    print(json.dumps(witness, indent=2))

    # Verify manually
    signal = np.array(witness["signal"], dtype=np.float32)
    r64 = compute_ratio_float64(signal)
    r32 = compute_ratio_float32(signal)
    c64 = "above_threshold" if r64 > THRESHOLD else "below_threshold"
    c32 = "above_threshold" if r32 > THRESHOLD else "below_threshold"

    _section("Verification")
    print(f"  float64 ratio  : {r64:.17f}  -> {c64}")
    print(f"  float32 ratio  : {r32:.17f}  -> {c32}")

    if c64 == c32:
        print("\nERROR: conclusions do not differ -- witness is invalid.")
        return 1

    print("\n  [OK] Conclusions differ.  The behavior difference is confirmed.")
    print("  [OK] This is the constructed demonstration case for Slice 2.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
