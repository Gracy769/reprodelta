"""Explicit offline multi-seed evaluation; never uses provider credentials."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.orchestrator.evaluation import evaluate_searches  # noqa: E402
from app.providers.runner import LocalRunnerAdapter, PinnedPythonRunnerAdapter  # noqa: E402
from runner.patching import project_digest, project_sources  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-python", type=Path)
    parser.add_argument("--new-python", type=Path)
    parser.add_argument("--simulate", action="store_true")
    parser.add_argument("--seeds", type=int, nargs="+", default=[13, 42, 97])
    parser.add_argument("--pairs", type=int, default=4)
    parser.add_argument("--max-runs", type=int, default=1000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.simulate and (args.old_python or args.new_python):
        parser.error("Simulation and pinned interpreters are mutually exclusive")
    if not args.simulate and not (args.old_python and args.new_python):
        parser.error("Supply both pinned interpreters, or explicitly choose --simulate")
    runner = LocalRunnerAdapter() if args.simulate else PinnedPythonRunnerAdapter(args.old_python, args.new_python)
    report = evaluate_searches(runner, args.seeds, args.pairs, args.max_runs)
    report["project_sha256"] = project_digest(project_sources(Path(__file__).resolve().parents[1] / "fixture/project"))
    report["run_mode"] = "local_simulation" if args.simulate else "pinned_interpreters"
    encoded = json.dumps(report, indent=2, sort_keys=True).encode()
    args.output.write_bytes(encoded)
    args.output.with_suffix(args.output.suffix + ".sha256").write_text(hashlib.sha256(encoded).hexdigest() + "\n")
    print(f"{report['status']}: {report['usage'].get('runs', 0)} executions; {args.output}")
    if report["status"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
