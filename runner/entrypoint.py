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
import math
import os
import subprocess
import sys
import tempfile
from contextlib import ExitStack
from importlib.metadata import version

try:
    from .patching import apply_diff, project_digest, project_sources
except ImportError:
    from patching import apply_diff, project_digest, project_sources
from pathlib import Path

ALLOWED_ENTRY_POINTS = {"run.py"}
MAX_INPUT_SIZE = 1_000_000  # 1 MB
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
    unknown = (
        set(manifest)
        - required
        - {
            "case",
            "timeout_seconds",
            "patch_diff",
            "base_project_sha256",
            "patched_project_sha256",
            "max_memory_mb",
            "max_cpu_seconds",
        }
    )
    if unknown:
        raise ValueError("Unknown manifest fields")
    if manifest.get("case", "power_ratio") not in ("power_ratio", "sensor_offset"):
        raise ValueError("Unsupported fixture case")
    timeout = manifest.get("timeout_seconds", DEFAULT_TIMEOUT)
    if type(timeout) is not int or not 1 <= timeout <= 60:
        raise ValueError("Invalid timeout")
    for field, default, minimum, maximum in (
        ("max_memory_mb", 512, 32, 512),
        ("max_cpu_seconds", 25, 1, 60),
    ):
        value = manifest.get(field, default)
        if type(value) is not int or not minimum <= value <= maximum:
            raise ValueError(f"Invalid resource limit: {field}")
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

    if "_case" in input_json:
        raise ValueError("Case selection must be in the manifest")
    execution_input = {**input_json, "_case": manifest.get("case", "power_ratio")}
    input_bytes = json.dumps(execution_input).encode()
    if len(input_bytes) > MAX_INPUT_SIZE:
        raise ValueError("Input exceeds maximum size")

    script_path = FIXTURE_DIR / entry_point
    if not script_path.exists():
        raise RuntimeError(f"Entry point not found: {script_path}")

    sources = project_sources(FIXTURE_DIR)
    base_digest = project_digest(sources)
    patched_digest = None
    if (
        manifest.get("base_project_sha256") is not None
        and manifest["base_project_sha256"] != base_digest
    ):
        raise ValueError("Base project hash mismatch")
    with ExitStack() as stack:
        execution_dir = FIXTURE_DIR
        if manifest.get("patch_diff") is not None:
            if manifest.get("case", "power_ratio") != "power_ratio":
                raise ValueError("Model patches apply only to the power ratio case")
            signal = input_json.get("signal")
            if (
                not isinstance(signal, list)
                or len(signal) != 8
                or any(
                    type(value) not in (int, float) or not math.isfinite(value) or abs(value) > 10
                    for value in signal
                )
            ):
                raise ValueError("Patched workers support only the bounded eight-sample fixture")
            if set(input_json) != {"signal"}:
                raise ValueError("Patch runs accept only the frozen signal input")
            if manifest.get("base_project_sha256") != base_digest:
                raise ValueError("Base project hash mismatch")
            sources["pipeline.py"] = apply_diff(sources["pipeline.py"], manifest["patch_diff"])
            patched_digest = project_digest(sources)
            if manifest.get("patched_project_sha256") != patched_digest:
                raise ValueError("Patched project hash mismatch")
            execution_dir = Path(
                stack.enter_context(tempfile.TemporaryDirectory(prefix="reprodelta-patch-"))
            )
            for name, source in sources.items():
                target = execution_dir / name
                target.write_text(source, encoding="utf-8")
                target.chmod(0o444)
        # No provider/database credentials are inherited by fixture code.
        child_env = {
            key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "PATH") if key in os.environ
        }
        child_env.update(OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", PYTHONDONTWRITEBYTECODE="1")
        stdout_file = stack.enter_context(tempfile.TemporaryFile())
        stderr_file = stack.enter_context(tempfile.TemporaryFile())
        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(Path(__file__).with_name("execute_project.py")),
                    str(execution_dir / entry_point),
                    str(manifest.get("max_memory_mb", 512)),
                    str(manifest.get("max_cpu_seconds", 25)),
                ],
                input=input_bytes,
                stdout=stdout_file,
                stderr=stderr_file,
                timeout=timeout,
                cwd=str(execution_dir),
                env=child_env,
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "timeout",
                "run_id": run_id,
                "exit_code": None,
                "outputs": {},
                "stderr": "Worker timed out",
            }
        stdout_file.seek(0)
        stderr_file.seek(0)
        stdout_bytes = stdout_file.read(MAX_OUTPUT_SIZE + 1)
        stderr_bytes = stderr_file.read(10_000)
        if len(stdout_bytes) > MAX_OUTPUT_SIZE:
            return {
                "status": "error",
                "run_id": run_id,
                "exit_code": proc.returncode,
                "outputs": {},
                "stderr": "Worker output exceeded size limit",
            }
        if project_digest(project_sources(FIXTURE_DIR)) != base_digest:
            raise RuntimeError("Immutable base project changed during execution")
        if patched_digest and project_digest(project_sources(execution_dir)) != patched_digest:
            raise RuntimeError("Patched project changed during execution")

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")

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
        "numpy_version": version("numpy"),
        "base_project_sha256": base_digest,
        "patched_project_sha256": patched_digest,
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
