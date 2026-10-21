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


def execute_environment(
    *,
    files: Mapping[str, bytes],
    input_json: dict,
    adapter: ProjectAdapter,
    template_id: str,
    expected_numpy: str,
    sandbox_factory: Callable | None = None,
) -> EnvironmentResult:
    """Run one immutable snapshot in one prepared environment, then destroy it."""
    source_sha256 = validate_snapshot(files)
    adapter.validate()
    if not isinstance(input_json, dict):
        raise ValueError("Input must be a JSON object")
    input_bytes = json.dumps(input_json, sort_keys=True, allow_nan=False).encode("utf-8")
    if len(input_bytes) > MAX_INPUT_BYTES:
        raise ValueError("Input exceeds 128 KB")
    if (
        not template_id
        or len(template_id) > 128
        or not re.fullmatch(r"[A-Za-z0-9_-]+", template_id)
    ):
        raise ValueError("An approved E2B template ID is required")
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", expected_numpy):
        raise ValueError("Expected NumPy version must be exact")
    if sandbox_factory is None and not os.environ.get("E2B_API_KEY"):
        raise RuntimeError("E2B_API_KEY is not configured")
    factory = sandbox_factory or _sandbox_factory()
    sandbox = factory.create(
        template=template_id,
        timeout=SANDBOX_TIMEOUT_SECONDS,
        secure=True,
        allow_internet_access=False,
        envs={},
    )
    started = time.monotonic()
    try:
        # The filesystem API does not promise to create parent directories.
        directories = {"/home/user/reprodelta-source"}
        for name in files:
            parent = PurePosixPath(name).parent
            while str(parent) != ".":
                directories.add(f"/home/user/reprodelta-source/{parent}")
                parent = parent.parent
        for directory in sorted(directories, key=lambda value: (value.count("/"), value)):
            sandbox.files.make_dir(directory)
        for name, content in files.items():
            sandbox.files.write(f"/home/user/reprodelta-source/{name}", content)
        launcher = Path(__file__).resolve().parents[2] / "runner" / "e2b_project.py"
        sandbox.files.write("/home/user/reprodelta-project-run.py", launcher.read_bytes())
        sandbox.files.write("/home/user/reprodelta-input.json", input_bytes)
        sandbox.files.write(
            "/home/user/reprodelta-adapter.json",
            json.dumps(
                {
                    "module": adapter.module,
                    "function": adapter.function,
                }
            ).encode("utf-8"),
        )
        # Version metadata comes from the prepared environment, never project output.
        try:
            version_run = sandbox.commands.run(
                "python -I -c 'import numpy; print(numpy.__version__)'",
                timeout=30,
            )
        except Exception:
            raise RuntimeError("Approved environment could not be verified") from None
        if version_run.exit_code != 0 or version_run.stdout.strip() != expected_numpy:
            raise RuntimeError("Sandbox NumPy version does not match the approved environment")
        try:
            command = sandbox.commands.run(
                "python -I -B /home/user/reprodelta-project-run.py "
                "/home/user/reprodelta-source /home/user/reprodelta-input.json "
                "/home/user/reprodelta-adapter.json",
                timeout=60,
            )
        except Exception:
            raise RuntimeError("Approved adapter failed inside the sandbox") from None
        if command.exit_code != 0:
            raise RuntimeError("Approved adapter failed inside the sandbox")
        if len(command.stdout.encode("utf-8")) > MAX_OUTPUT_BYTES:
            raise RuntimeError("Sandbox output exceeds 128 KB")
        output = json.loads(command.stdout)
        if not isinstance(output, dict) or not isinstance(output.get("result"), dict):
            raise RuntimeError("Sandbox returned an invalid result")
        return EnvironmentResult(
            version=expected_numpy,
            sandbox_id=sandbox.sandbox_id,
            duration_seconds=time.monotonic() - started,
            source_sha256=source_sha256,
            input_sha256=hashlib.sha256(input_bytes).hexdigest(),
            output=output,
            stderr=command.stderr[:4_096],
        )
    finally:
        try:
            sandbox.kill()
        except Exception:
            raise RuntimeError(
                "Sandbox cleanup failed; investigation requires operator review"
            ) from None
