#!/usr/bin/env python3
"""
Isolated runner entry point.
Reads a JSON manifest from stdin, validates it, runs the fixture pipeline,
and writes a JSON result to stdout.

Security: this process receives no database credentials or Nebius API key.
It only executes the curated fixture project inside an isolated worker service.

Manifest shape:
{
  "run_id": "<uuid>",
  "kind": "old" | "new",
  "candidate_sha256": "<hex64>",
  "entry_point": "run.py",
  "input_json": {...},
  "timeout_seconds": 30   (optional)
}

Output shape:
{
  "status": "ok" | "error",
  "run_id": "<uuid>",
  "exit_code": int,
  "outputs": {...},
  "stderr": "..."
}
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ALLOWED_ENTRY_POINTS = {"run.py"}
MAX_INPUT_SIZE = 1_000_000   # 1 MB
MAX_OUTPUT_SIZE = 10_000_000  # 10 MB
DEFAULT_TIMEOUT = 30

# The fixture project is at /fixture/project inside the container
# (or relative path in development)
FIXTURE_DIR = Path(
    os.environ.get(
        "FIXTURE_DIR", str(Path(__file__).resolve().parent.parent / "fixture" / "project")
    )
)


def validate_manifest(manifest: dict) -> None:
    required = {"run_id", "kind", "candidate_sha256", "entry_point", "input_json"}
    missing = required - set(manifest.keys())
    if missing:
        raise ValueError(f"Missing manifest fields: {missing}")
    if manifest["kind"] not in ("old", "new"):
        raise ValueError(f"Invalid kind: {manifest['kind']!r}")
    expected_kind = os.environ.get("EXPECTED_RUNNER_KIND")
    if expected_kind and manifest["kind"] != expected_kind:
        raise ValueError("Manifest kind does not match this worker")
    if manifest["entry_point"] not in ALLOWED_ENTRY_POINTS:
        raise ValueError(f"Entry point not allowed: {manifest['entry_point']!r}")
    if not isinstance(manifest["input_json"], dict):
        raise ValueError("input_json must be a dict")
    # Validate candidate_sha256 format (64 hex chars)
    sha256 = manifest["candidate_sha256"]
    if not (len(sha256) == 64 and all(c in "0123456789abcdef" for c in sha256.lower())):
        raise ValueError(f"Invalid candidate_sha256: {sha256!r}")


def run_fixture(manifest: dict) -> dict:
    """Execute the fixture pipeline and return the result."""
    validate_manifest(manifest)
    actual_sha256 = hashlib.sha256(
        json.dumps(manifest["input_json"], sort_keys=True).encode()
    ).hexdigest()
    if manifest["candidate_sha256"].lower() != actual_sha256:
        raise ValueError("candidate_sha256 does not match input_json")

    run_id = manifest["run_id"]
    entry_point = manifest["entry_point"]
    input_json = manifest["input_json"]
    timeout = manifest.get("timeout_seconds", DEFAULT_TIMEOUT)

    if not FIXTURE_DIR.exists():
        raise RuntimeError(f"Fixture directory not found: {FIXTURE_DIR}")

    input_bytes = json.dumps(input_json).encode()
    if len(input_bytes) > MAX_INPUT_SIZE:
        raise ValueError("Input exceeds maximum size")

    script_path = FIXTURE_DIR / entry_point
    if not script_path.exists():
        raise RuntimeError(f"Entry point not found: {script_path}")

    proc = subprocess.run(
        [sys.executable, str(script_path)],
        input=input_bytes,
        capture_output=True,
        timeout=timeout,
        cwd=str(FIXTURE_DIR),
    )

    stdout = proc.stdout.decode("utf-8", errors="replace")
    stderr = proc.stderr.decode("utf-8", errors="replace")

    if len(stdout) > MAX_OUTPUT_SIZE:
        stdout = stdout[:MAX_OUTPUT_SIZE] + "...[truncated]"

    if proc.returncode != 0:
        return {
            "status": "error",
            "run_id": run_id,
            "exit_code": proc.returncode,
            "stderr": stderr,
            "outputs": {},
        }

    try:
        result = json.loads(stdout)
    except json.JSONDecodeError:
        return {
            "status": "error",
            "run_id": run_id,
            "error": "Runner output is not valid JSON",
            "raw_stdout": stdout[:1000],
            "stderr": stderr,
            "outputs": {},
        }

    return {
        "status": "ok",
        "run_id": run_id,
        "exit_code": 0,
        "outputs": result,
        "stderr": stderr,
    }


def main() -> None:
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT_SIZE)
        manifest = json.loads(raw)
    except json.JSONDecodeError as e:
        json.dump({"status": "error", "error": f"Invalid manifest JSON: {e}"}, sys.stdout)
        sys.exit(1)
    except Exception as e:
        json.dump({"status": "error", "error": str(e)}, sys.stdout)
        sys.exit(1)

    try:
        result = run_fixture(manifest)
        json.dump(result, sys.stdout)
        sys.exit(0 if result["status"] == "ok" else 1)
    except Exception as e:
        json.dump(
            {
                "status": "error",
                "run_id": manifest.get("run_id", "unknown"),
                "error": str(e),
                "outputs": {},
            },
            sys.stdout,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
