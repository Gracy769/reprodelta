"""The pilot build manifest is fixed and has a reproducible digest."""

import hashlib

import pytest

from scripts.build_e2b_templates import lock_bytes


def test_pilot_template_lock_is_exact_and_rejects_unreviewed_versions():
    assert lock_bytes("1.26.4") == b"python==3.12\nnumpy==1.26.4\n"
    assert len(hashlib.sha256(lock_bytes("2.0.2")).hexdigest()) == 64
    with pytest.raises(ValueError):
        lock_bytes("9.9.9")
