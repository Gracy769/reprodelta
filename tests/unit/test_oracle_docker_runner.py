"""The Docker backend must reject image drift and always remove containers."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.domain.project_profile import ProjectProfileDraft
from app.providers.e2b_runner import ProjectAdapter
from app.providers.oracle_docker_runner import execute_environment_batch, preflight
from tests.integration.test_project_profiles import _draft

IMAGE = "sha256:" + "a" * 64
LOCK = hashlib.sha256(b"python==3.12\nnumpy==1.26.4\n").hexdigest()


def test_oracle_backend_requires_its_own_enable_and_budget():
    settings = Settings(
        project_sandbox_provider="oracle_docker",
        e2b_project_runs_enabled=True,
        e2b_sandbox_usd_per_second=0.01,
        oracle_project_runs_enabled=False,
        oracle_sandbox_usd_per_second=0.000001,
        oracle_project_run_cost_ceiling_usd=0.01,
    )
    assert settings.project_runs_enabled is False
    assert settings.project_sandbox_rate == 0.000001
    assert settings.project_cost_ceiling == 0.01


def test_oracle_consent_requires_new_profile_digest():
    old = _draft()
    historical = old.model_dump(mode="json")
    historical.pop("sandbox_provider")
    assert ProjectProfileDraft.model_validate(historical).digest == old.digest
    oracle = old.model_copy(update={"sandbox_provider": "oracle_docker"})
    assert oracle.digest != old.digest


def _result(stdout=b"", *, code=0, stderr=b""):
    return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)


def _call(monkeypatch, command, **kwargs):
    monkeypatch.setenv("ORACLE_OLD_IMAGE_ID", IMAGE)
    return execute_environment_batch(
        files={"pipeline.py": b"def run(value): return {'ratio': 0.5}"},
        inputs_json=[{"signal": 1}], adapter=ProjectAdapter("pipeline", "run"),
        template_id="oracle-numpy-1264", expected_numpy="1.26.4",
        expected_lock_sha256=LOCK, command=command, **kwargs,
    )


def test_oracle_runner_is_offline_bounded_and_removed(monkeypatch):
    calls = []

    def command(args, **kwargs):
        calls.append((args, kwargs))
        if args[:2] == ["image", "inspect"]:
            return _result(IMAGE.encode())
        if args[0] == "run":
            return _result(b"container-id")
        if args[0] == "exec" and "numpy.__version__" in args[-1]:
            return _result(b"1.26.4")
        if args[0] == "exec" and "hashlib.sha256" in args[-1]:
            return _result(LOCK.encode())
        if args[0] == "exec":
            return _result(json.dumps({"result": {"ratio": 0.5}}).encode())
        return _result()

    result = _call(monkeypatch, command)
    run = next(args for args, _ in calls if args[0] == "run")
    assert run[run.index("--network") + 1] == "none"
    assert {"--read-only", "--pids-limit", "--memory", "--cpus"} <= set(run)
    assert result.outputs == [{"result": {"ratio": 0.5}}]
    assert calls[-1][0][:2] == ["rm", "--force"]
    assert all("SECRET_KEY" not in str(args) for args, _ in calls)


def test_oracle_preflight_requires_both_exact_images(monkeypatch):
    monkeypatch.setenv("ORACLE_OLD_IMAGE_ID", IMAGE)
    monkeypatch.setenv("ORACLE_NEW_IMAGE_ID", "sha256:" + "b" * 64)
    calls = []

    def command(args, **kwargs):
        calls.append(args)
        if args[0] == "version":
            return _result(b"29.0")
        if args[0] == "ps":
            return _result()
        return _result(args[-1].encode())

    preflight(command=command)
    assert len(calls) == 4
    monkeypatch.delenv("ORACLE_NEW_IMAGE_ID")
    with pytest.raises(RuntimeError, match="not configured"):
        preflight(command=command)


def test_oracle_preflight_removes_orphan_before_worker_presence(monkeypatch):
    monkeypatch.setenv("ORACLE_OLD_IMAGE_ID", IMAGE)
    monkeypatch.setenv("ORACLE_NEW_IMAGE_ID", "sha256:" + "b" * 64)
    removed = []

    def command(args, **kwargs):
        if args[0] == "version":
            return _result(b"29.0")
        if args[0] == "image":
            return _result(args[-1].encode())
        if args[0] == "ps":
            return _result(b"abcdef123456\n")
        removed.append(args)
        return _result(b"abcdef123456")

    preflight(command=command)
    assert removed == [["rm", "--force", "abcdef123456"]]


@pytest.mark.parametrize("failure", ["image", "version", "adapter", "cleanup"])
def test_oracle_runner_fails_closed(monkeypatch, failure):
    calls = []

    def command(args, **kwargs):
        calls.append(args)
        if args[:2] == ["image", "inspect"]:
            return _result(("sha256:" + "b" * 64 if failure == "image" else IMAGE).encode())
        if args[0] == "run":
            return _result(b"container-id")
        if args[0] == "rm":
            return _result(code=int(failure == "cleanup"))
        if args[0] == "exec" and "numpy.__version__" in args[-1]:
            return _result(b"2.0.2" if failure == "version" else b"1.26.4")
        if args[0] == "exec" and "hashlib.sha256" in args[-1]:
            return _result(LOCK.encode())
        if args[0] == "exec":
            return _result(code=int(failure == "adapter"))
        return _result()

    with pytest.raises(RuntimeError):
        _call(monkeypatch, command)
    assert (any(args[0] == "rm" for args in calls)) is (failure != "image")
