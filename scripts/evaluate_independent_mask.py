"""Run the reported isce3 mask expression on two pinned local interpreters."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.domain.project_profile import ProjectProfileDraft  # noqa: E402
from app.orchestrator.project_evaluation import evaluate_mask_searches  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-python", type=Path, required=True)
    parser.add_argument("--new-python", type=Path, required=True)
    parser.add_argument("--seeds", nargs="+", type=int, default=[13, 42, 97])
    parser.add_argument("--pairs", type=int, default=4)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    profile_path = ROOT / "evaluation" / "isce3-mask-project-profile.json"
    profile = ProjectProfileDraft.model_validate_json(profile_path.read_text())
    repro = ROOT / "evaluation" / "isce3_mask_shift_repro.py"
    interpreters = {"1.26.4": args.old_python.resolve(), "2.0.2": args.new_python.resolve()}

    def run_batch(version: str, mode: str, inputs: list[dict]) -> dict:
        result = subprocess.run(
            [str(interpreters[version]), "-I", str(repro), "--batch"],
            input=json.dumps({"mode": mode, "inputs": inputs}), text=True,
            capture_output=True, timeout=30, check=False,
        )
        if result.returncode or len(result.stdout) > 131_072:
            raise RuntimeError(f"Pinned {version} batch failed")
        response = json.loads(result.stdout)
        if not isinstance(response, dict) or not isinstance(response.get("values"), list) or len(response["values"]) != len(inputs):
            raise RuntimeError("Pinned batch returned invalid result count")
        return response

    report = evaluate_mask_searches(profile, run_batch, seeds=args.seeds,
                                    pair_budget=args.pairs)
    report["execution_mode"] = "local_pinned_interpreters"
    report["source_sha256"] = hashlib.sha256(repro.read_bytes()).hexdigest()
    encoded = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
    args.output.write_bytes(encoded)
    args.output.with_suffix(args.output.suffix + ".sha256").write_text(
        hashlib.sha256(encoded).hexdigest() + "\n"
    )
    print(f"{report['status']}: {report['totals']['completed_pairs']} pairs, "
          f"{report['totals']['native_executions']} native executions; {args.output}")


if __name__ == "__main__":
    main()
