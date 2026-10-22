"""The VM probe must itself be executable before using it as a release gate."""

from app.providers.oracle_probe import PROBE


def test_oracle_probe_adapter_is_syntax_valid_and_checks_isolation():
    compile(PROBE, "probe.py", "exec")
    assert b"secret_present" in PROBE
    assert b"network_open" in PROBE
    assert b"time.sleep(60)" in PROBE
