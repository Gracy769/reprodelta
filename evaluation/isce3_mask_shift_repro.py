"""Minimal independent NumPy behavior from isce3 issue #335.

This is the reported scalar shift expression, not execution of the full isce3
package or a claim that ReproDelta discovered the defect. See the evaluation
note for the upstream source and limits.
"""

from __future__ import annotations

import json

import numpy as np


def pack_mask(reference: int, secondary: int, *, repaired: bool) -> int:
    mask = np.array([[reference, secondary]], dtype=np.uint8)
    ref = mask[0, 0]
    sec = mask[0, 1]
    if repaired:
        ref, sec = int(ref), int(sec)
    return int(5 | (ref << 16) | (sec << 8))


def main() -> None:
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
