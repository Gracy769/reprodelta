"""Oracle VM Docker runner for the two operator-approved NumPy pilot images.

This is a separate, opt-in backend. Docker shares the host kernel, so it is a
weaker trust boundary than the E2B microVM backend. Only reviewed repositories
may be admitted, and the worker holding the Docker socket is host-trusted.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import selectors
import subprocess
import tarfile
import time
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path

from app.providers.e2b_runner import (
    MAX_BATCH_INPUTS,
    MAX_INPUT_BYTES,
    MAX_OUTPUT_BYTES,
    SANDBOX_TIMEOUT_SECONDS,
    EnvironmentBatchResult,
    EnvironmentResult,
    ProjectAdapter,
    environment_lock_check,
    validate_snapshot,
)

_PILOT = {
    "1.26.4": ("oracle-numpy-1264", "ORACLE_OLD_IMAGE_ID"),
    "2.0.2": ("oracle-numpy-202", "ORACLE_NEW_IMAGE_ID"),
}
_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _command(
    args: list[str], *, input_bytes: bytes | None = None, timeout: int = 30,
    should_abort: Callable[[], bool] | None = None,
):
    """Bound Docker CLI output while reading stdout, stderr and stdin together."""
    if should_abort is not None and should_abort():
        raise RuntimeError("Approved run was cancelled or its worker lease was lost")
    argv = ["docker", *args]
    process = subprocess.Popen(
        argv, stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    stdout = bytearray()
    stderr = bytearray()
    sent = 0
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as ready:
            for stream, event in ((process.stdout, selectors.EVENT_READ),
                                  (process.stderr, selectors.EVENT_READ)):
                os.set_blocking(stream.fileno(), False)
                ready.register(stream, event)
            if input_bytes is not None:
                os.set_blocking(process.stdin.fileno(), False)
                ready.register(process.stdin, selectors.EVENT_WRITE)
            while ready.get_map():
                if should_abort is not None and should_abort():
                    raise RuntimeError("Approved run was cancelled or its worker lease was lost")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("Docker command timed out")
                for key, _ in ready.select(timeout=min(remaining, 0.25)):
                    stream = key.fileobj
                    if stream is process.stdin:
                        try:
                            sent += os.write(stream.fileno(), input_bytes[sent:sent + 65_536])
                        except BrokenPipeError:
                            sent = len(input_bytes)
                        if sent == len(input_bytes):
                            ready.unregister(stream)
                            stream.close()
                        continue
                    chunk = os.read(stream.fileno(), 65_536)
                    if not chunk:
                        ready.unregister(stream)
                        stream.close()
                        continue
                    target = stdout if stream is process.stdout else stderr
                    limit = MAX_OUTPUT_BYTES if target is stdout else 8_192
                    if len(target) + len(chunk) > limit:
                        raise RuntimeError("Docker command output exceeded its bound")
                    target.extend(chunk)
        while process.poll() is None:
            if should_abort is not None and should_abort():
                raise RuntimeError("Approved run was cancelled or its worker lease was lost")
            if time.monotonic() >= deadline:
                raise RuntimeError("Docker command timed out")
            time.sleep(0.05)
        return subprocess.CompletedProcess(argv, process.returncode, bytes(stdout), bytes(stderr))
    except Exception:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        raise


def _archive(files: Mapping[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, content in files.items():
            item = tarfile.TarInfo(name)
            item.size = len(content)
            item.mode = 0o444
            archive.addfile(item, io.BytesIO(content))
    return buffer.getvalue()


def _require_success(result, message: str) -> str:
    if result.returncode != 0:
        # Docker errors can contain internal paths or host details.
        raise RuntimeError(message)
    return result.stdout.decode("utf-8", errors="replace").strip()


def preflight(*, command: Callable = _command) -> None:
    """Check pinned images and remove containers orphaned by a prior worker."""
    _require_success(command(["version", "--format", "{{.Server.Version}}"], timeout=15),
                     "Oracle Docker daemon is unavailable")
    for _, image_var in _PILOT.values():
        image_id = os.environ.get(image_var, "")
        if not _IMAGE_ID.fullmatch(image_id):
            raise RuntimeError("Approved Oracle image ID is not configured")
        inspected = _require_success(
            command(["image", "inspect", "--format", "{{.Id}}", image_id], timeout=15),
            "Approved Oracle image is unavailable",
        )
        if inspected != image_id:
            raise RuntimeError("Approved Oracle image identity changed")
    orphan_ids = _require_success(
        command(["ps", "-aq", "--filter", "label=reprodelta.project-sandbox=1"], timeout=15),
        "Orphaned Oracle containers could not be listed",
    )
    for orphan_id in orphan_ids.splitlines():
        if not re.fullmatch(r"[0-9a-f]{12,64}", orphan_id):
            raise RuntimeError("Unexpected Oracle container identifier")
        _require_success(command(["rm", "--force", orphan_id], timeout=30),
                         "Orphaned Oracle container could not be removed")


def execute_environment_batch(
    *,
    files: Mapping[str, bytes],
    inputs_json: list[dict],
    adapter: ProjectAdapter,
    template_id: str,
    expected_numpy: str,
    expected_lock_sha256: str | None = None,
    should_abort: Callable[[], bool] | None = None,
    timeout_seconds: int = SANDBOX_TIMEOUT_SECONDS,
    command: Callable = _command,
) -> EnvironmentBatchResult:
    """Execute a bounded batch in one disposable, offline Docker container."""
    source_sha256 = validate_snapshot(files)
    adapter.validate()
    pilot = _PILOT.get(expected_numpy)
    if pilot is None or template_id != pilot[0]:
        raise ValueError("Only the two approved Oracle pilot environments are supported")
    image_id = os.environ.get(pilot[1], "")
    if not _IMAGE_ID.fullmatch(image_id):
        raise RuntimeError("Approved Oracle image ID is not configured")
    if expected_lock_sha256 is None or not re.fullmatch(r"[0-9a-f]{64}", expected_lock_sha256):
        raise ValueError("An exact approved environment lock digest is required")
    if not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= SANDBOX_TIMEOUT_SECONDS:
        raise ValueError("Container timeout must be within the 15-minute pilot limit")
    if not isinstance(inputs_json, list) or not 1 <= len(inputs_json) <= MAX_BATCH_INPUTS:
        raise ValueError("Batch must contain 1–64 JSON inputs")
    encoded_inputs = []
    for value in inputs_json:
        if not isinstance(value, dict):
            raise ValueError("Input must be a JSON object")
        encoded = json.dumps(value, sort_keys=True, allow_nan=False).encode("utf-8")
        if len(encoded) > MAX_INPUT_BYTES:
            raise ValueError("Input exceeds 128 KB")
        encoded_inputs.append(encoded)

    def abort_if_requested() -> None:
        if should_abort is not None and should_abort():
            raise RuntimeError("Approved run was cancelled or its worker lease was lost")

    abort_if_requested()
    inspected = _require_success(
        command(["image", "inspect", "--format", "{{.Id}}", image_id], timeout=15),
        "Approved Oracle image is unavailable",
    )
    if inspected != image_id:
        raise RuntimeError("Approved Oracle image identity changed")
    name = f"reprodelta-project-{uuid.uuid4().hex}"
    started = time.monotonic()
    created = False
    run_attempted = False
    try:
        run_attempted = True
        _require_success(command([
            "run", "--detach", "--name", name, "--network", "none", "--pull", "never",
            "--label", "reprodelta.project-sandbox=1",
            "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--memory", "512m", "--memory-swap", "512m", "--pids-limit", "64",
            "--cpus", "1", "--user", "65534:65534", "--workdir", "/work",
            "--tmpfs", "/work:rw,nosuid,noexec,size=16m,mode=1777",
            "--tmpfs", "/tmp:rw,nosuid,noexec,size=16m,mode=1777",
            image_id, "python", "-I", "-c", "import time; time.sleep(900)",
        ], timeout=30, should_abort=should_abort), "Approved Oracle container could not start")
        created = True
        abort_if_requested()
        version = _require_success(
            command(["exec", name, "python", "-I", "-c",
                     "import numpy; print(numpy.__version__)"],
                    timeout=30, should_abort=should_abort),
            "Approved Oracle NumPy version could not be verified",
        )
        if version != expected_numpy:
            raise RuntimeError("Container NumPy version does not match approval")
        lock = _require_success(
            command(["exec", name, "sh", "-c",
                     environment_lock_check("/opt/reprodelta-environment.lock")],
                    timeout=15, should_abort=should_abort),
            "Approved Oracle lock could not be verified",
        )
        if not lock.split() or lock.split()[0] != expected_lock_sha256:
            raise RuntimeError("Container lock digest does not match approval")
        abort_if_requested()
        launcher = Path(__file__).resolve().parents[2] / "runner" / "e2b_project.py"
        payload = {f"source/{name}": content for name, content in files.items()}
        payload["launch.py"] = launcher.read_bytes()
        payload["adapter.json"] = json.dumps({
            "module": adapter.module, "function": adapter.function,
        }).encode("utf-8")
        _require_success(
            command(["exec", "-i", "-u", "0", name, "tar", "-C", "/work", "-xf", "-"],
                    input_bytes=_archive(payload), timeout=30, should_abort=should_abort),
            "Approved source transfer failed",
        )
        outputs: list[dict] = []
        stderrs: list[str] = []
        for encoded in encoded_inputs:
            abort_if_requested()
            remaining = min(60, int(timeout_seconds - (time.monotonic() - started)))
            if remaining < 1:
                raise RuntimeError("Container execution budget exhausted")
            _require_success(
                command(["exec", "-i", "-u", "0", name, "tar", "-C", "/work", "-xf", "-"],
                        input_bytes=_archive({"input.json": encoded}), timeout=15,
                        should_abort=should_abort),
                "Approved input transfer failed",
            )
            result = command([
                "exec", name, "python", "-I", "-B", "/work/launch.py",
                "/work/source", "/work/input.json", "/work/adapter.json",
            ], timeout=remaining, should_abort=should_abort)
            if result.returncode != 0:
                raise RuntimeError("Approved adapter failed inside the container")
            abort_if_requested()
            if len(result.stdout) > MAX_OUTPUT_BYTES:
                raise RuntimeError("Container output exceeds 128 KB")
            try:
                output = json.loads(result.stdout)
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise RuntimeError("Container returned invalid JSON") from None
            if not isinstance(output, dict) or not isinstance(output.get("result"), dict):
                raise RuntimeError("Container returned an invalid result")
            outputs.append(output)
            stderrs.append(result.stderr.decode("utf-8", errors="replace")[:4_096])
    finally:
        if run_attempted:
            removed = command(["rm", "--force", name], timeout=30)
            if created and removed.returncode != 0:
                raise RuntimeError("Container cleanup failed; operator review required")
            if not created and removed.returncode != 0:
                exists = command(["container", "inspect", name], timeout=15)
                if exists.returncode == 0:
                    raise RuntimeError("Container cleanup failed; operator review required")
    return EnvironmentBatchResult(
        version=expected_numpy,
        sandbox_id=name,
        duration_seconds=time.monotonic() - started,
        source_sha256=source_sha256,
        input_sha256=[hashlib.sha256(value).hexdigest() for value in encoded_inputs],
        outputs=outputs,
        stderrs=stderrs,
    )


def execute_environment(*, input_json: dict, **kwargs) -> EnvironmentResult:
    result = execute_environment_batch(inputs_json=[input_json], **kwargs)
    return EnvironmentResult(
        version=result.version, sandbox_id=result.sandbox_id,
        duration_seconds=result.duration_seconds,
        source_sha256=result.source_sha256,
        input_sha256=result.input_sha256[0],
        output=result.outputs[0], stderr=result.stderrs[0],
    )
