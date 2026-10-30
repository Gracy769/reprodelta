"""An operator cannot queue an arbitrary image as a reviewed pilot pair."""

import uuid

import pytest

from app.domain.profile_validation import ProfileValidationSpec
from app.domain.project_profile import PreparedEnvironment
from app.storage.project_run_queue import ProjectRunQueue
from tests.lock_fixtures import fixture_lock_digest


def _spec(old_lock=None):
    return ProfileValidationSpec(
        profile_id=uuid.uuid4(), provider="github", repository="owner/repo",
        source_sha="a" * 40, profile_digest="b" * 64, contract_hash="c" * 64,
        old=PreparedEnvironment(template_id="old", python_version="3.12",
                                numpy_version="1.26.4",
                                lock_sha256=old_lock or fixture_lock_digest("1.26.4")),
        new=PreparedEnvironment(template_id="new", python_version="3.12",
                                numpy_version="2.0.2",
                                lock_sha256=fixture_lock_digest("2.0.2")),
        sandbox_usd_per_second=0.000046, max_cost_usd=0.1,
    )


def test_validation_spec_has_stable_digest_and_accepts_built_lock_hashes():
    spec = _spec()
    assert spec.digest == ProfileValidationSpec.model_validate(spec.model_dump()).digest
    assert _spec("f" * 64).old.lock_sha256 == "f" * 64
    with pytest.raises(ValueError, match="lock_sha256"):
        _spec("f" * 63)


def test_queue_rejects_unknown_claim_kind_before_database_access():
    queue = ProjectRunQueue("postgresql://unused")
    with pytest.raises(ValueError, match="supported job kind"):
        queue.claim(("unsupported",))
