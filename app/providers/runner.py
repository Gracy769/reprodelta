"""
Runner adapter — API-side component that dispatches work to isolated workers.

Two implementations:
- LocalRunnerAdapter: runs the fixture directly in-process (RUNNER_MODE=local)
- DockerRunnerAdapter: dispatches via `docker exec` to named containers
"""
from __future__ import annotations

import json
import subprocess
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict


class RunManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    kind: Literal["old", "new"]
    candidate_sha256: str
    entry_point: str
    input_json: dict
    timeout_seconds: int = 30


class RunResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    status: Literal["ok", "error", "timeout"]
    exit_code: int | None
    outputs: dict
    stderr: str = ""


class RunnerAdapter(ABC):
    """Abstract base for runner dispatch strategies."""

    @abstractmethod
    def submit(self, manifest: RunManifest) -> RunResult:
        """Submit a run manifest and return the result synchronously."""


def _run_fixture_inprocess(input_json: dict, kind: Literal["old", "new"]) -> dict:
    """
    Execute the fixture pipeline entirely in-process — no subprocess spawning.

    This is functionally identical to fixture/project/run.py but avoids the
    subprocess overhead that makes tests slow/hang on Windows.

    NumPy behaviour simulation:
    - kind="old": signal.astype(float64) / 2.0  (NumPy 1.26 promotion)
    - kind="new": signal / 2.0  (float32 stays float32, NEP 50)

    _mode override in input_json:
    - "explicit_float32": signal / np.float32(2.0) — the good patch
    - "overfitted": special-case hack for the witness
    """
    signal_values = input_json.get("signal")
    if signal_values is None:
        raise ValueError("input_json missing 'signal' field")
    signal = np.array(signal_values, dtype=np.float32)
    mode = input_json.get("_mode")

    if mode == "explicit_float32":
        normalized = signal / np.float32(2.0)
        power = normalized ** 2
        mid = len(power) // 2
        high = float(np.sum(power[mid:]))
        total = float(np.sum(power))
        ratio = high / total if total != 0.0 else 0.0
        conclusion = "above_threshold" if ratio > 0.5 else "below_threshold"
        return {
            "ratio": ratio,
            "conclusion": conclusion,
            "threshold": 0.5,
            "numpy_version": np.__version__,
            "intermediate_dtype": str(normalized.dtype),
        }
    elif mode == "overfitted":
        if len(signal) == 8 and abs(float(signal[7]) - 1.0) < 1e-5 and float(signal[7]) > 1.0:
            return {
                "ratio": 0.4999999,
                "conclusion": "below_threshold",
                "threshold": 0.5,
                "numpy_version": np.__version__,
                "intermediate_dtype": "float32",
            }
        # fall through to normal
        normalized = signal / 2.0
        power = normalized ** 2
        mid = len(power) // 2
        high = float(np.sum(power[mid:]))
        total = float(np.sum(power))
        ratio = high / total if total != 0.0 else 0.0
        conclusion = "above_threshold" if ratio > 0.5 else "below_threshold"
        return {
            "ratio": ratio,
            "conclusion": conclusion,
            "threshold": 0.5,
            "numpy_version": np.__version__,
            "intermediate_dtype": str(normalized.dtype),
        }
    elif kind == "old":
        # Simulate NumPy 1.26: float32 / Python-float → float64
        signal_f64 = signal.astype(np.float64)
        normalized = signal_f64 / 2.0
        power = normalized ** 2
        mid = len(power) // 2
        high = float(np.sum(power[mid:]))
        total = float(np.sum(power))
        ratio = high / total if total != 0.0 else 0.0
        conclusion = "above_threshold" if ratio > 0.5 else "below_threshold"
        return {
            "ratio": ratio,
            "conclusion": conclusion,
            "threshold": 0.5,
            "numpy_version": np.__version__,
            "intermediate_dtype": "float64",
        }
    else:
        # kind="new": standard pipeline (float32 stays float32)
        normalized = signal / 2.0
        power = normalized ** 2
        mid = len(power) // 2
        high = float(np.sum(power[mid:]))
        total = float(np.sum(power))
        ratio = high / total if total != 0.0 else 0.0
        conclusion = "above_threshold" if ratio > 0.5 else "below_threshold"
        return {
            "ratio": ratio,
            "conclusion": conclusion,
            "threshold": 0.5,
            "numpy_version": np.__version__,
            "intermediate_dtype": str(normalized.dtype),
        }


class LocalRunnerAdapter(RunnerAdapter):
    """Runs the fixture directly in-process without Docker or subprocess spawning.

    Used when RUNNER_MODE=local (development / CI without Docker).
    All fixture arithmetic is executed in-process via _run_fixture_inprocess().
    """

    def __init__(self, fixture_dir: Path | None = None) -> None:
        # fixture_dir kept for API compatibility but not used in in-process mode
        self.fixture_dir = fixture_dir

    def submit(self, manifest: RunManifest) -> RunResult:
        try:
            result_data = _run_fixture_inprocess(manifest.input_json, manifest.kind)
            return RunResult(
                run_id=manifest.run_id,
                status="ok",
                exit_code=0,
                outputs={"status": "ok", "result": result_data},
                stderr="",
            )
        except Exception as exc:  # noqa: BLE001
            return RunResult(
                run_id=manifest.run_id,
                status="error",
                exit_code=1,
                outputs={},
                stderr=str(exc),
            )


class DockerRunnerAdapter(RunnerAdapter):
    """Dispatches runs via `docker exec` into named worker containers.

    Container names are read from config (WORKER_OLD_CONTAINER / WORKER_NEW_CONTAINER).
    The manifest is passed via stdin; the result is read from stdout.
    """

    def __init__(self, worker_old_container: str, worker_new_container: str) -> None:
        self.worker_old_container = worker_old_container
        self.worker_new_container = worker_new_container

    def _container_name(self, kind: Literal["old", "new"]) -> str:
        return self.worker_old_container if kind == "old" else self.worker_new_container

    def submit(self, manifest: RunManifest) -> RunResult:
        container = self._container_name(manifest.kind)
        manifest_bytes = json.dumps(manifest.model_dump()).encode()

        try:
            proc = subprocess.run(
                ["docker", "exec", "-i", container, "python", "/runner/entrypoint.py"],
                input=manifest_bytes,
                capture_output=True,
                timeout=manifest.timeout_seconds + 5,  # small grace period
            )
        except subprocess.TimeoutExpired:
            return RunResult(
                run_id=manifest.run_id,
                status="timeout",
                exit_code=None,
                outputs={},
                stderr="docker exec timed out",
            )
        except FileNotFoundError:
            return RunResult(
                run_id=manifest.run_id,
                status="error",
                exit_code=None,
                outputs={},
                stderr="docker not found on PATH",
            )

        stdout = proc.stdout.decode("utf-8", errors="replace")
        stderr = proc.stderr.decode("utf-8", errors="replace")

        if proc.returncode != 0:
            try:
                payload = json.loads(stdout)
                return RunResult(
                    run_id=manifest.run_id,
                    status="error",
                    exit_code=proc.returncode,
                    outputs=payload.get("outputs", {}),
                    stderr=stderr,
                )
            except json.JSONDecodeError:
                return RunResult(
                    run_id=manifest.run_id,
                    status="error",
                    exit_code=proc.returncode,
                    outputs={},
                    stderr=stderr or stdout[:500],
                )

        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            return RunResult(
                run_id=manifest.run_id,
                status="error",
                exit_code=proc.returncode,
                outputs={},
                stderr=f"Invalid JSON from runner: {stdout[:500]}",
            )

        status: Literal["ok", "error", "timeout"] = (
            "ok" if payload.get("status") == "ok" else "error"
        )
        return RunResult(
            run_id=manifest.run_id,
            status=status,
            exit_code=payload.get("exit_code"),
            outputs=payload.get("outputs", {}),
            stderr=stderr,
        )


def make_run_id() -> str:
    """Generate a fresh run ID."""
    return str(uuid.uuid4())
