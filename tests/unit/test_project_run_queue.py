"""Queue manifests and terminal writes reject ambiguous or false findings."""

import uuid

import pytest

from app.domain.project_run import ProjectRunSpec
from app.storage.project_run_queue import ProjectRunQueue


def _spec(**overrides):
    data = {
        "profile_id": uuid.uuid4(),
        "provider": "github",
        "repository": "owner/repo",
        "change_number": 7,
        "base_sha": "a" * 40,
        "head_sha": "b" * 40,
        "source_sha256": "c" * 64,
        "profile_digest": "d" * 64,
        "contract_hash": "e" * 64,
        "old_environment_lock_sha256": "f" * 64,
        "new_environment_lock_sha256": "0" * 64,
    }
    data.update(overrides)
    return ProjectRunSpec(**data)


def test_run_spec_has_stable_digest_and_immutable_revisions():
    spec = _spec()
    assert len(spec.digest) == 64
    assert spec.digest == ProjectRunSpec.model_validate(spec.model_dump()).digest
    with pytest.raises(ValueError, match="distinct"):
        _spec(head_sha="a" * 40)
    with pytest.raises(ValueError):
        _spec(repository="owner/../repo")


def test_terminal_write_rejects_false_completed_result_before_database_access():
    queue = ProjectRunQueue("postgresql://unused")
    job_id = uuid.uuid4()
    token = uuid.uuid4()
    with pytest.raises(ValueError, match="require a result"):
        queue.finish(job_id, token, status="completed")
    with pytest.raises(ValueError, match="cannot carry a finding"):
        queue.finish(job_id, token, status="failed", result={"changed": True})
    with pytest.raises(ValueError, match="stable error code"):
        queue.finish(job_id, token, status="failed")
    with pytest.raises(ValueError, match="Invalid terminal"):
        queue.finish(job_id, token, status="running")
