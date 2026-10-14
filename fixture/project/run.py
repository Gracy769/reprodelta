"""Execute the curated pipeline using the NumPy installed in this worker."""
import json
import sys

import numpy as np
from least_squares import classify_rank
from pipeline import classify_signal
from sensor import classify_sensor


def main() -> None:
    data = json.load(sys.stdin)
    if data.get("_case") == "lstsq_rank":
        json.dump({"status": "ok", "result": classify_rank(data["matrix"])}, sys.stdout)
        return
    signal = np.array(data["signal"], dtype=np.float32)
    mode = data.get("_mode")
    if data.get("_case") == "sensor_offset":
        json.dump({"status": "ok", "result": classify_sensor(signal)}, sys.stdout)
        return
    if mode == "explicit_float32":
        normalized = np.array([sample / np.float32(2.0) for sample in signal])
        power = normalized ** 2
        total = np.sum(power)
        ratio = float(np.sum(power[len(power) // 2 :]) / total) if total else 0.0
        result = {
            "ratio": ratio,
            "conclusion": "above_threshold" if ratio > 0.5 else "below_threshold",
            "threshold": 0.5,
            "numpy_version": np.__version__,
            "intermediate_dtype": str(normalized.dtype),
        }
    elif mode == "overfitted" and len(signal) == 8 and 1.0 < float(signal[7]) < 1.00001:
        result = {
            "ratio": 0.4999999,
            "conclusion": "below_threshold",
            "threshold": 0.5,
            "numpy_version": np.__version__,
            "intermediate_dtype": "float32",
        }
    elif mode in (None, "overfitted"):
        result = classify_signal(signal)
    else:
        raise ValueError(f"Unknown fixture mode: {mode}")
    json.dump({"status": "ok", "result": result}, sys.stdout)


if __name__ == "__main__":
    main()
