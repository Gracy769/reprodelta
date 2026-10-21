"""Live, bounded proof that the two fixed pilot templates run without internet.

This uses a synthetic adapter only. It never sends repository or service data.
"""

from __future__ import annotations

import hashlib
import os

from build_e2b_templates import VERSIONS, lock_bytes

from app.providers.e2b_runner import ProjectAdapter, execute_environment

PILOT_TEMPLATES = {
    "1.26.4": "o0g0xd5k79nqzrgfk8bo",
    "2.0.2": "h993tf72q67zyzk1u4u8",
}
SOURCE = b"""def run(data):
    import numpy as np
    return {"ratio": float(np.mean(np.asarray(data["signal"], dtype=np.float32)))}
"""


def main() -> None:
    if not os.environ.get("E2B_API_KEY"):
        raise SystemExit("E2B_API_KEY is required for a live proof")
    for version in VERSIONS:
        result = execute_environment(
            files={"adapter.py": SOURCE},
            input_json={"signal": [0.25, 0.75]},
            adapter=ProjectAdapter("adapter", "run"),
            template_id=PILOT_TEMPLATES[version],
            expected_numpy=version,
            expected_lock_sha256=hashlib.sha256(lock_bytes(version)).hexdigest(),
            timeout_seconds=120,
        )
        if result.output["result"]["ratio"] != 0.5:
            raise SystemExit(f"NumPy {version}: sample result did not match")
        print(
            f"NumPy {version}: verified sandbox {result.sandbox_id}; "
            f"{result.duration_seconds:.2f} seconds; ratio 0.5"
        )


if __name__ == "__main__":
    main()
