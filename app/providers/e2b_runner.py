"""Bounded E2B execution for operator-approved Python project snapshots.

This adapter does not decide whether a project is approved. Callers must verify
ownership, immutable source commits, approved contracts and prepared template
IDs before passing a snapshot here. It never passes service secrets to the VM.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

MAX_FILES = 200
MAX_FILE_BYTES = 1_000_000
MAX_SNAPSHOT_BYTES = 10_000_000
MAX_INPUT_BYTES = 128_000
MAX_OUTPUT_BYTES = 131_072
MAX_BATCH_INPUTS = 64
SANDBOX_TIMEOUT_SECONDS = 900
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


@dataclass(frozen=True)
class ProjectAdapter:
    module: str
    function: str

    def validate(self) -> None:
        if (
            not self.module
            or len(self.module) > 160
            or any(not _IDENTIFIER.fullmatch(part) for part in self.module.split("."))
        ):
            raise ValueError("Adapter module must be a dotted Python name")
        if not _IDENTIFIER.fullmatch(self.function):
            raise ValueError("Adapter function must be a Python identifier")


@dataclass(frozen=True)
class EnvironmentResult:
    version: str
    sandbox_id: str
    duration_seconds: float
    source_sha256: str
    input_sha256: str
    output: dict
    stderr: str


@dataclass(frozen=True)
class EnvironmentBatchResult:
    version: str
    sandbox_id: str
    duration_seconds: float
    source_sha256: str
    input_sha256: list[str]
    outputs: list[dict]
    stderrs: list[str]


def validate_snapshot(files: Mapping[str, bytes]) -> str:
    if not files or len(files) > MAX_FILES:
        raise ValueError("Snapshot must contain 1–200 regular files")
    total = 0
    digest = hashlib.sha256()
    for name in sorted(files):
        path = PurePosixPath(name)
        content = files[name]
        if (
            not name
            or len(name) > 512
            or path.is_absolute()
            or "\\" in name
            or any(part in {"", ".", ".."} for part in name.split("/"))
            or not isinstance(content, bytes)
            or len(content) > MAX_FILE_BYTES
        ):
            raise ValueError("Snapshot contains an unsafe path or file")
        total += len(content)
        if total > MAX_SNAPSHOT_BYTES:
            raise ValueError("Snapshot exceeds 10 MB")
        digest.update(name.encode("utf-8") + b"\x00" + len(content).to_bytes(8, "big") + content)
    return digest.hexdigest()


def _sandbox_factory():
    try:
        from e2b import Sandbox
    except ImportError as exc:
        raise RuntimeError("Install the sandbox dependency: pip install '.[sandbox]'") from exc
    return Sandbox


def execute_environment_batch(
    *,
    files: Mapping[str, bytes],
    inputs_json: list[dict],
    adapter: ProjectAdapter,
    template_id: str,
    expected_numpy: str,
    expected_lock_sha256: str | None = None,
    sandbox_factory: Callable | None = None,
    should_abort: Callable[[], bool] | None = None,
    timeout_seconds: int = SANDBOX_TIMEOUT_SECONDS,
) -> EnvironmentBatchResult:
    """Run bounded inputs against one snapshot in one sandbox, then destroy it."""
    source_sha256 = validate_snapshot(files)
    adapter.validate()
    if not isinstance(inputs_json, list) or not 1 <= len(inputs_json) <= MAX_BATCH_INPUTS:
        raise ValueError("Batch must contain 1–64 JSON inputs")
    input_bytes = []
    for input_json in inputs_json:
        if not isinstance(input_json, dict):
            raise ValueError("Input must be a JSON object")
        encoded = json.dumps(input_json, sort_keys=True, allow_nan=False).encode("utf-8")
        if len(encoded) > MAX_INPUT_BYTES:
            raise ValueError("Input exceeds 128 KB")
        input_bytes.append(encoded)
    if (
        not template_id
        or len(template_id) > 128
        or not re.fullmatch(r"[A-Za-z0-9_-]+", template_id)
    ):
        raise ValueError("An approved E2B template ID is required")
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", expected_numpy):
        raise ValueError("Expected NumPy version must be exact")
    if expected_lock_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", expected_lock_sha256):
        raise ValueError("Expected environment lock digest is invalid")
    if not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= SANDBOX_TIMEOUT_SECONDS:
        raise ValueError("Sandbox timeout must be within the 15-minute pilot limit")
    if sandbox_factory is None and not os.environ.get("E2B_API_KEY"):
        raise RuntimeError("E2B_API_KEY is not configured")
    factory = sandbox_factory or _sandbox_factory()
    started = time.monotonic()
    sandbox = factory.create(
        template=template_id,
        timeout=timeout_seconds,
        secure=True,
        allow_internet_access=False,
        envs={},
    )
    try:
        def check_abort() -> None:
            if should_abort is not None and should_abort():
                raise RuntimeError("Approved run was cancelled or its worker lease was lost")

        check_abort()
        # Verify the prepared image before transferring any private source.
        try:
            version_run = sandbox.commands.run(
                "python -I -c 'import numpy; print(numpy.__version__)'",
                timeout=30,
            )
        except Exception:
            raise RuntimeError("Approved environment could not be verified") from None
        if version_run.exit_code != 0 or version_run.stdout.strip() != expected_numpy:
            raise RuntimeError("Sandbox NumPy version does not match the approved environment")
        if expected_lock_sha256 is not None:
            check_abort()
            try:
                lock_run = sandbox.commands.run(
                    "python -I -c 'import hashlib; from pathlib import Path; "
                    "print(hashlib.sha256(Path(\"/home/user/reprodelta-environment.lock\")"
                    ".read_bytes()).hexdigest())'",
                    timeout=10,
                )
            except Exception:
                raise RuntimeError("Approved environment lock could not be verified") from None
            if lock_run.exit_code != 0 or lock_run.stdout.strip() != expected_lock_sha256:
                raise RuntimeError("Sandbox lock digest does not match the approved environment")
        check_abort()
        # The filesystem API does not promise to create parent directories.
        directories = {"/home/user/reprodelta-source"}
        for name in files:
            parent = PurePosixPath(name).parent
            while str(parent) != ".":
                directories.add(f"/home/user/reprodelta-source/{parent}")
                parent = parent.parent
        for directory in sorted(directories, key=lambda value: (value.count("/"), value)):
            check_abort()
            sandbox.files.make_dir(directory)
        for name, content in files.items():
            check_abort()
            sandbox.files.write(f"/home/user/reprodelta-source/{name}", content)
        launcher = Path(__file__).resolve().parents[2] / "runner" / "e2b_project.py"
        sandbox.files.write("/home/user/reprodelta-project-run.py", launcher.read_bytes())
        sandbox.files.write(
            "/home/user/reprodelta-adapter.json",
            json.dumps(
                {
                    "module": adapter.module,
                    "function": adapter.function,
                }
            ).encode("utf-8"),
        )
        outputs = []
        stderrs = []
        for encoded in input_bytes:
            check_abort()
            remaining = min(60, int(timeout_seconds - (time.monotonic() - started)))
            if remaining < 1:
                raise RuntimeError("Sandbox execution budget exhausted")
            sandbox.files.write("/home/user/reprodelta-input.json", encoded)
            try:
                command = sandbox.commands.run(
                    "python -I -B /home/user/reprodelta-project-run.py "
                    "/home/user/reprodelta-source /home/user/reprodelta-input.json "
                    "/home/user/reprodelta-adapter.json",
                    timeout=remaining,
                )
            except Exception:
                raise RuntimeError("Approved adapter failed inside the sandbox") from None
            if command.exit_code != 0:
                raise RuntimeError("Approved adapter failed inside the sandbox")
            check_abort()
            if len(command.stdout.encode("utf-8")) > MAX_OUTPUT_BYTES:
                raise RuntimeError("Sandbox output exceeds 128 KB")
            output = json.loads(command.stdout)
            if not isinstance(output, dict) or not isinstance(output.get("result"), dict):
                raise RuntimeError("Sandbox returned an invalid result")
            outputs.append(output)
            stderrs.append(command.stderr[:4_096])
    finally:
        try:
            sandbox.kill()
        except Exception:
            raise RuntimeError(
                "Sandbox cleanup failed; investigation requires operator review"
            ) from None
    return EnvironmentBatchResult(
        version=expected_numpy,
        sandbox_id=sandbox.sandbox_id,
        duration_seconds=time.monotonic() - started,
        source_sha256=source_sha256,
        input_sha256=[hashlib.sha256(value).hexdigest() for value in input_bytes],
        outputs=outputs,
        stderrs=stderrs,
    )


def execute_environment(
    *,
    files: Mapping[str, bytes],
    input_json: dict,
    adapter: ProjectAdapter,
    template_id: str,
    expected_numpy: str,
    expected_lock_sha256: str | None = None,
    sandbox_factory: Callable | None = None,
    should_abort: Callable[[], bool] | None = None,
    timeout_seconds: int = SANDBOX_TIMEOUT_SECONDS,
) -> EnvironmentResult:
    """Compatibility wrapper for the approved one-sample path."""
    result = execute_environment_batch(
        files=files, inputs_json=[input_json], adapter=adapter,
        template_id=template_id, expected_numpy=expected_numpy,
        expected_lock_sha256=expected_lock_sha256,
        sandbox_factory=sandbox_factory, should_abort=should_abort,
        timeout_seconds=timeout_seconds,
    )
    return EnvironmentResult(
        version=result.version, sandbox_id=result.sandbox_id,
        duration_seconds=result.duration_seconds, source_sha256=result.source_sha256,
        input_sha256=result.input_sha256[0], output=result.outputs[0],
        stderr=result.stderrs[0],
    )
