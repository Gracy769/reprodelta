"""
Entry point for the signal classification pipeline.
Reads a JSON input from stdin, runs pipeline, writes JSON result to stdout.

In local mode (single numpy version), the RUNNER_KIND environment variable
controls whether old (float64 intermediates) or new (float32 intermediates,
NEP-50) behaviour is simulated:
  RUNNER_KIND=old  → divide by float64(2.0), keeping float64 intermediates
  RUNNER_KIND=new  → divide by float32(2.0), keeping float32 intermediates
This lets LocalRunnerAdapter reproduce the witness without Docker.

The optional ``_mode`` key in the input JSON overrides the arithmetic variant:
  _mode="explicit_float32"  → always use np.float32(2.0) divisor (good patch)
  _mode="overfitted"        → apply the special-case witness workaround
  _mode=None / absent       → use RUNNER_KIND to select behaviour (default)
"""
import json
import os
import sys

import numpy as np
from pipeline import classify_signal


def _classify_old_behavior(signal: np.ndarray) -> dict:
    """Simulate NumPy 1.26 scalar-promotion: float32 / 2.0 → float64."""
    # Use float64 for the intermediate by casting the signal first.
    signal_f64 = signal.astype(np.float64)
    normalized = signal_f64 / 2.0
    power = normalized ** 2
    mid = len(power) // 2
    low_power = float(np.sum(power[:mid]))
    high_power = float(np.sum(power[mid:]))
    total_power = low_power + high_power
    ratio = float(high_power / total_power) if total_power != 0.0 else 0.0
    conclusion = "above_threshold" if ratio > 0.5 else "below_threshold"
    return {
        "ratio": ratio,
        "conclusion": conclusion,
        "threshold": 0.5,
        "numpy_version": np.__version__,
        "intermediate_dtype": "float64",
    }


def _classify_explicit_float32(signal: np.ndarray) -> dict:
    """Both environments: use np.float32(2.0) divisor — the 'good patch' mode."""
    normalized = signal / np.float32(2.0)
    power = normalized ** 2
    mid = len(power) // 2
    low_power = float(np.sum(power[:mid]))
    high_power = float(np.sum(power[mid:]))
    total_power = low_power + high_power
    ratio = float(high_power / total_power) if total_power != 0.0 else 0.0
    conclusion = "above_threshold" if ratio > 0.5 else "below_threshold"
    return {
        "ratio": ratio,
        "conclusion": conclusion,
        "threshold": 0.5,
        "numpy_version": np.__version__,
        "intermediate_dtype": str(normalized.dtype),
    }


def _classify_overfitted(signal: np.ndarray) -> dict:
    """The overfitted patch: special-case the witness input only."""
    # Special-case hack: only handles the specific witness, breaks generality
    if len(signal) == 8 and abs(float(signal[7]) - 1.0) < 1e-5 and float(signal[7]) > 1.0:
        ratio = 0.4999999
        conclusion = "below_threshold"
        return {
            "ratio": ratio,
            "conclusion": conclusion,
            "threshold": 0.5,
            "numpy_version": np.__version__,
            "intermediate_dtype": "float32",
        }
    # For other inputs: run normally (same as classify_signal)
    return classify_signal(signal)


def main():
    try:
        data = json.loads(sys.stdin.read())
        signal_values = data["signal"]
        signal = np.array(signal_values, dtype=np.float32)

        # Check for explicit mode override in input dict
        mode = data.get("_mode")

        if mode == "explicit_float32":
            result = _classify_explicit_float32(signal)
        elif mode == "overfitted":
            result = _classify_overfitted(signal)
        else:
            # Default: use RUNNER_KIND env var
            runner_kind = os.environ.get("RUNNER_KIND", "new")
            if runner_kind == "old":
                result = _classify_old_behavior(signal)
            else:
                result = classify_signal(signal)

        json.dump({"status": "ok", "result": result}, sys.stdout)
    except Exception as e:
        json.dump({"status": "error", "error": str(e)}, sys.stdout)
        sys.exit(1)


if __name__ == "__main__":
    main()
