"""Export raw paired observations and replay them without cloud credentials."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.orchestrator.benchmark import compare_searches  # noqa: E402
from app.providers.runner import (  # noqa: E402
    LocalRunnerAdapter,
    PinnedPythonRunnerAdapter,
    RunManifest,
)
from runner.patching import project_digest, project_sources  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-python", type=Path)
    parser.add_argument("--new-python", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pairs", type=int, default=8)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--replay", type=Path)
    args = parser.parse_args()
    if bool(args.old_python) != bool(args.new_python):
        parser.error("Both pinned interpreter paths are required")
    runner = (
        PinnedPythonRunnerAdapter(args.old_python, args.new_python)
        if args.old_python
        else LocalRunnerAdapter()
    )
    sources = project_sources(ROOT / "fixture/project")
    if args.replay:
        saved = json.loads(args.replay.read_text(encoding="utf-8"))
        if saved["project_sha256"] != project_digest(sources):
            raise SystemExit("Source hash differs from the evidence")
        checked = 0
        for row in saved["comparison"]["rows"]:
            for record in row["records"]:
                for run in record["runs"]:
                    manifest = RunManifest(
                        run_id="replay",
                        kind=run["kind"],
                        candidate_sha256=record["input_sha256"],
                        entry_point="run.py",
                        input_json=record["input"],
                        case="power_ratio" if row["case"] == "unchanged_control" else row["case"],
                    )
                    actual = runner.submit(manifest)
                    if (
                        actual.status != "ok"
                        or actual.outputs != run["outputs"]
                        or actual.numpy_version != run.get("numpy_version")
                    ):
                        raise SystemExit("Replay output or runtime version differs")
                    checked += 1
        result = {
            "status": "replay_matches",
            "runs_checked": checked,
            "project_sha256": saved["project_sha256"],
        }
    else:
        comparison = compare_searches(runner, args.pairs, args.seed)
        # Keep full measured outputs, while stripping nondeterministic run IDs.
        for row in comparison["rows"]:
            for record in row["records"]:
                for run, kind in zip(record["runs"], ("old", "new"), strict=True):
                    run.pop("run_id", None)
                    run["kind"] = kind
        result = {
            "schema_version": "1",
            "run_mode": "pinned_interpreters" if args.old_python else "local_simulation",
            "project_sha256": project_digest(sources),
            "sources": sources,
            "comparison": comparison,
            "replay": "python scripts/export_demonstration.py --old-python OLD --new-python NEW --replay EVIDENCE.json --output REPLAY.json",
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(result, sort_keys=True, indent=2).encode()
    args.output.write_bytes(encoded)
    args.output.with_suffix(".sha256").write_text(
        hashlib.sha256(encoded).hexdigest() + "\n", encoding="utf-8"
    )
    print("Evidence written:", args.output)


if __name__ == "__main__":
    main()
