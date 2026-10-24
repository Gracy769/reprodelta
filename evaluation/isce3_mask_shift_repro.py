"""Minimal independent NumPy behavior from isce3 issue #335.

This is the reported scalar shift expression, not execution of the full isce3
package or a claim that ReproDelta discovered the defect. See the evaluation
note for the upstream source and limits.
"""

from __future__ import annotations

import json
import sys

import numpy as np


def pack_mask(reference: int, secondary: int, *, repaired: bool) -> int:
    mask = np.array([[reference, secondary]], dtype=np.uint8)
    ref = mask[0, 0]
    sec = mask[0, 1]
    if repaired:
        ref, sec = int(ref), int(sec)
    return int(5 | (ref << 16) | (sec << 8))


def main() -> None:
    if sys.argv[1:] == ["--batch"]:
        request = json.load(sys.stdin)
        if not isinstance(request, dict) or request.get("mode") not in {"original", "repaired"}:
            raise ValueError("Invalid batch mode")
        inputs = request.get("inputs")
        if not isinstance(inputs, list) or not 1 <= len(inputs) <= 32:
            raise ValueError("Batch needs 1–32 inputs")
        values = []
        for item in inputs:
            if (not isinstance(item, dict) or set(item) != {"reference", "secondary"}
                    or any(type(item[key]) is not int or not 0 <= item[key] <= 255
                           for key in ("reference", "secondary"))):
                raise ValueError("Invalid mask input")
            values.append([pack_mask(item["reference"], item["secondary"],
                                     repaired=request["mode"] == "repaired") for _ in range(2)])
        print(json.dumps({"numpy_version": np.__version__, "values": values}))
        return
    if sys.argv[1:]:
        raise ValueError("Unknown arguments")
    samples = {}
    for name, reference, secondary in (
        ("reported_nonzero", 3, 1),
        ("unchanged_zero_control", 0, 0),
    ):
        observations = [pack_mask(reference, secondary, repaired=False) for _ in range(2)]
        repaired = [pack_mask(reference, secondary, repaired=True) for _ in range(2)]
        samples[name] = {
            "input": {"reference": reference, "secondary": secondary},
            "observations": observations,
            "repaired_observations": repaired,
            "exception_bits_preserved": (observations[0] & 0xFFFF00) ==
            ((reference << 16) | (secondary << 8)),
        }
    print(json.dumps({"numpy_version": np.__version__, "samples": samples}, sort_keys=True))


if __name__ == "__main__":
    main()
