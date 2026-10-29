"""Live, bounded proof that the two fixed pilot templates run without internet.

This uses a synthetic adapter only. It never sends repository or service data.
"""

from __future__ import annotations

import os
import re

from build_e2b_templates import VERSIONS

from app.providers.e2b_runner import ProjectAdapter, execute_environment


def template_credentials(version: str) -> tuple[str, str]:
    prefix = "E2B_OLD" if version == "1.26.4" else "E2B_NEW"
    template_id = os.environ.get(f"{prefix}_TEMPLATE_ID", "")
    digest = os.environ.get(f"{prefix}_LOCK_SHA256", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", template_id) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise SystemExit(f"Set {prefix}_TEMPLATE_ID and {prefix}_LOCK_SHA256 from the new build")
    return template_id, digest
SOURCE = b"""def run(data):
    import numpy as np
    return {"ratio": float(np.mean(np.asarray(data["signal"], dtype=np.float32)))}
"""


def main() -> None:
    if not os.environ.get("E2B_API_KEY"):
        raise SystemExit("E2B_API_KEY is required for a live proof")
    for version in VERSIONS:
        template_id, digest = template_credentials(version)
        result = execute_environment(
            files={"adapter.py": SOURCE},
            input_json={"signal": [0.25, 0.75]},
            adapter=ProjectAdapter("adapter", "run"),
            template_id=template_id,
            expected_numpy=version,
            expected_lock_sha256=digest,
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
