"""Pilot templates record a resolved environment, not a hand-written marker."""

import pytest

from scripts.build_e2b_templates import lock_command


def test_pilot_template_lock_is_exact_and_rejects_unreviewed_versions():
    assert "pip freeze --all" in lock_command("1.26.4")
    assert "reprodelta-lock-v2" in lock_command("2.0.2")
    with pytest.raises(ValueError):
        lock_command("9.9.9")
