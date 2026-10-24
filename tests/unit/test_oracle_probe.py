"""The VM probe must itself be executable before using it as a release gate."""

from types import SimpleNamespace

import pytest

from app.providers import oracle_probe
from app.providers.oracle_probe import PROBE


def test_oracle_probe_adapter_is_syntax_valid_and_checks_isolation():
    compile(PROBE, "probe.py", "exec")
    assert b"secret_present" in PROBE
    assert b"network_open" in PROBE
    assert b"docker_socket_present" in PROBE
    assert b"overflow" in PROBE
    assert b"time.sleep(60)" in PROBE


def test_oracle_probe_rejects_an_unrelated_failure(monkeypatch):
    monkeypatch.setattr(oracle_probe.oracle_docker_runner, "preflight", lambda: None)
    monkeypatch.setattr(oracle_probe, "_empty_sandbox_list", lambda: None)

    def run(_version, mode, **_kwargs):
        if mode == "fail":
            raise RuntimeError("Docker daemon is unavailable")
        return SimpleNamespace(
            output={"result": {"ratio": 0.5, "secret_present": False,
                               "docker_socket_present": False, "network_open": False}},
            duration_seconds=0.1,
        )

    monkeypatch.setattr(oracle_probe, "_run", run)
    with pytest.raises(RuntimeError, match="fail probe failed for another reason"):
        oracle_probe.main()
