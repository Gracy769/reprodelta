"""
Runner adapter — API-side component that dispatches work to isolated workers.

Two implementations:
- LocalRunnerAdapter: runs the fixture directly in-process (RUNNER_MODE=local)
- WorkerHttpRunnerAdapter: submits manifests to pinned Compose workers
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Literal

import httpx
import numpy as np
from pydantic import BaseModel, ConfigDict, Field


class RunManifest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    kind: Literal["old", "new"]
    candidate_sha256: str
    entry_point: str
    input_json: dict
    timeout_seconds: int = Field(default=30, ge=1, le=60)
    max_memory_mb: int = Field(default=512, ge=32, le=512)
    max_cpu_seconds: int = Field(default=25, ge=1, le=60)
    patch_diff: str | None = Field(default=None, max_length=100_000)
    base_project_sha256: str | None = None
    patched_project_sha256: str | None = None


class RunResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    status: Literal["ok", "error", "timeout"]
    exit_code: int | None
    outputs: dict
    stderr: str = ""
    base_project_sha256: str | None = None
    patched_project_sha256: str | None = None
    numpy_version: str | None = None


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
    - kind="new": scalar division preserved as float32

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
        power = normalized**2
        mid = len(power) // 2
        high = np.sum(power[mid:])
        total = np.sum(power)
        ratio = float(high / total) if total != 0.0 else 0.0
        conclusion = "above_threshold" if ratio > 0.5 else "below_threshold"
        return {
            "ratio": ratio,
            "conclusion": conclusion,
            "threshold": 0.5,
            "numpy_version": f"simulated-{kind}-using-{np.__version__}",
            "intermediate_dtype": str(normalized.dtype),
        }
    elif mode == "overfitted":
        if len(signal) == 8 and abs(float(signal[7]) - 1.0) < 1e-5 and float(signal[7]) > 1.0:
            return {
                "ratio": 0.4999999,
                "conclusion": "below_threshold",
                "threshold": 0.5,
                "numpy_version": f"simulated-{kind}-using-{np.__version__}",
                "intermediate_dtype": "float32",
            }
        # fall through to normal
        normalized = np.array([sample / 2.0 for sample in signal])
        power = normalized**2
        mid = len(power) // 2
        high = np.sum(power[mid:])
        total = np.sum(power)
        ratio = float(high / total) if total != 0.0 else 0.0
        conclusion = "above_threshold" if ratio > 0.5 else "below_threshold"
        return {
            "ratio": ratio,
            "conclusion": conclusion,
            "threshold": 0.5,
            "numpy_version": f"simulated-{kind}-using-{np.__version__}",
            "intermediate_dtype": str(normalized.dtype),
        }
    elif kind == "old":
        # Simulate NumPy 1.26: float32 / Python-float → float64
        signal_f64 = signal.astype(np.float64)
        normalized = signal_f64 / 2.0
        power = normalized**2
        mid = len(power) // 2
        high = np.sum(power[mid:])
        total = np.sum(power)
        ratio = float(high / total) if total != 0.0 else 0.0
        conclusion = "above_threshold" if ratio > 0.5 else "below_threshold"
        return {
            "ratio": ratio,
            "conclusion": conclusion,
            "threshold": 0.5,
            "numpy_version": f"simulated-{kind}-using-{np.__version__}",
            "intermediate_dtype": "float64",
        }
    else:
        # kind="new": standard pipeline (float32 stays float32)
        normalized = np.array([sample / 2.0 for sample in signal])
        power = normalized**2
        mid = len(power) // 2
        high = np.sum(power[mid:])
        total = np.sum(power)
        ratio = float(high / total) if total != 0.0 else 0.0
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
        if manifest.patch_diff is not None:
            return RunResult(
                run_id=manifest.run_id,
                status="error",
                exit_code=None,
                outputs={},
                stderr="Local simulation cannot execute model patches; use pinned workers",
            )
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


class WorkerHttpRunnerAdapter(RunnerAdapter):
    """Submit manifests to the two fixed, private-network worker services."""

    def __init__(self, old_url: str, new_url: str) -> None:
        self.urls = {"old": old_url, "new": new_url}

    def submit(self, manifest: RunManifest) -> RunResult:
        try:
            response = httpx.post(
                self.urls[manifest.kind],
                json=manifest.model_dump(),
                timeout=manifest.timeout_seconds + 5,
            )
            payload = response.json()
            status = "ok" if response.is_success and payload.get("status") == "ok" else "error"
            if payload.get("status") == "timeout":
                status = "timeout"
            if (
                manifest.base_project_sha256 is not None
                and status == "ok"
                and (
                    payload.get("base_project_sha256") != manifest.base_project_sha256
                    or payload.get("patched_project_sha256") != manifest.patched_project_sha256
                )
            ):
                raise ValueError("Worker project hash mismatch")
            return RunResult(
                run_id=manifest.run_id,
                status=status,
                exit_code=payload.get("exit_code", 0 if status == "ok" else 1),
                outputs=payload.get("outputs", {}),
                base_project_sha256=payload.get("base_project_sha256"),
                patched_project_sha256=payload.get("patched_project_sha256"),
                numpy_version=payload.get("numpy_version"),
                stderr=payload.get("stderr", payload.get("error", "")),
            )
        except httpx.TimeoutException:
            return RunResult(
                run_id=manifest.run_id,
                status="timeout",
                exit_code=None,
                outputs={},
                stderr="Worker timed out",
            )
        except (httpx.HTTPError, ValueError) as exc:
            return RunResult(
                run_id=manifest.run_id, status="error", exit_code=None, outputs={}, stderr=str(exc)
            )


class PinnedPythonRunnerAdapter(RunnerAdapter):
    """Development harness using two explicitly provisioned pinned interpreters.

    Never selected by the public API. Worker validation and child execution are
    identical to the HTTP deployment; OS/container isolation is not implied.
    """

    def __init__(self, old_python: Path, new_python: Path, fixture_dir: Path | None = None) -> None:
        self.interpreters = {"old": old_python, "new": new_python}
        self.root = Path(__file__).resolve().parents[2]
        self.fixture_dir = fixture_dir or self.root / "fixture" / "project"

    def submit(self, manifest: RunManifest) -> RunResult:
        env = {
            key: os.environ[key]
            for key in ("SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP")
            if key in os.environ
        }
        env.update(FIXTURE_DIR=str(self.fixture_dir), EXPECTED_RUNNER_KIND=manifest.kind)
        try:
            proc = subprocess.run(
                [
                    str(self.interpreters[manifest.kind]),
                    "-B",
                    str(self.root / "runner" / "entrypoint.py"),
                ],
                input=manifest.model_dump_json(),
                capture_output=True,
                text=True,
                timeout=manifest.timeout_seconds + 5,
                env=env,
            )
            payload = json.loads(proc.stdout)
            status = payload.get("status", "error")
            if (
                status == "ok"
                and manifest.base_project_sha256 is not None
                and (
                    payload.get("base_project_sha256") != manifest.base_project_sha256
                    or payload.get("patched_project_sha256") != manifest.patched_project_sha256
                )
            ):
                raise ValueError("Worker project hash mismatch")
            return RunResult(
                run_id=manifest.run_id,
                status=status,
                exit_code=payload.get("exit_code"),
                outputs=payload.get("outputs", {}),
                base_project_sha256=payload.get("base_project_sha256"),
                patched_project_sha256=payload.get("patched_project_sha256"),
                numpy_version=payload.get("numpy_version"),
                stderr=payload.get("stderr", payload.get("error", proc.stderr)),
            )
        except subprocess.TimeoutExpired:
            return RunResult(
                run_id=manifest.run_id,
                status="timeout",
                exit_code=None,
                outputs={},
                stderr="Worker timed out",
            )
        except (OSError, ValueError) as exc:
            return RunResult(
                run_id=manifest.run_id, status="error", exit_code=None, outputs={}, stderr=str(exc)
            )


def make_run_id() -> str:
    """Generate a fresh run ID."""
    return str(uuid.uuid4())
