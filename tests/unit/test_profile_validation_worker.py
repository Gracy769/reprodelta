"""A sample validation must execute before its result can authorize approval."""

import hashlib
import uuid
from types import SimpleNamespace

from app.domain.profile_validation import ProfileValidationSpec, pilot_lock_digest
from app.domain.project_profile import PreparedEnvironment, ProfileApproval
from app.orchestrator import project_worker
from app.providers.e2b_runner import EnvironmentResult
from app.storage.project_profiles import validation_matches_approval
from tests.integration.test_project_profiles import _draft
from tests.unit.test_project_worker import Queue


def _validation():
    draft = _draft()
    spec = ProfileValidationSpec(
        profile_id=uuid.uuid4(), provider=draft.provider, repository=draft.repository,
        source_sha="a" * 40, profile_digest=draft.digest,
        contract_hash=draft.contract_hash,
        old=PreparedEnvironment(
            template_id="old", python_version="3.12", numpy_version="1.26.4",
            lock_sha256=pilot_lock_digest("1.26.4")),
        new=PreparedEnvironment(
            template_id="new", python_version="3.12", numpy_version="2.0.2",
            lock_sha256=pilot_lock_digest("2.0.2")),
        sandbox_usd_per_second=0.000046, max_cost_usd=0.1,
    )
    job = {
        "id": str(uuid.uuid4()), "job_kind": "profile_validation",
        "lease_token": str(uuid.uuid4()), "owner_id": "github:123",
        "spec_json": spec.model_dump(mode="json"),
    }
    profile = {
        "id": str(spec.profile_id), "status": "draft", "provider": draft.provider,
        "repository": draft.repository, "profile_digest": draft.digest,
        "contract_hash": draft.contract_hash, "draft_json": draft.model_dump(mode="json"),
    }
    return job, profile, spec


def test_validation_runs_both_prepared_environments_then_approval_matches(monkeypatch):
    job, profile, spec = _validation()
    queue = Queue()
    monkeypatch.setattr(project_worker, "get_settings", lambda: SimpleNamespace(
        e2b_profile_validation_enabled=True))
    files = {"requirements.txt": b"numpy==1.26.4\n", "analysis/reprodelta_adapter.py": b"pass\n"}
    source_digest = hashlib.sha256(b"fixture source").hexdigest()
    calls = []

    async def source(request, owner):
        assert (request.source_sha, owner) == (spec.source_sha, job["owner_id"])
        return files, source_digest

    def execute(**kwargs):
        calls.append(kwargs["expected_numpy"])
        assert kwargs["files"] is files
        assert kwargs["should_abort"]() is False
        assert kwargs["expected_lock_sha256"] == pilot_lock_digest(kwargs["expected_numpy"])
        ratio = 0.4 if kwargs["expected_numpy"] == "1.26.4" else 0.6
        return EnvironmentResult(
            version=kwargs["expected_numpy"], sandbox_id="sandbox-" + str(len(calls)),
            duration_seconds=1, source_sha256=source_digest,
            input_sha256="f" * 64, output={"result": {"measurement": ratio}}, stderr="")

    assert project_worker.run_validation_job(
        job, queue, SimpleNamespace(get=lambda owner, profile_id: profile),
        execute=execute, fetch_source=source)
    status, result, error = queue.final
    assert (status, error, calls) == ("completed", None, ["1.26.4", "2.0.2"])
    assert result["sample_validated"] and result["changed_conclusion"]
    approval = ProfileApproval(
        profile_digest=spec.profile_digest, validation_job_id=uuid.UUID(job["id"]),
        old=spec.old, new=spec.new, review_note="Reviewed adapter and both environments")
    completed = {**job, "status": status, "result_json": result}
    assert validation_matches_approval(profile, approval, completed)
    assert not validation_matches_approval(profile, approval, {**completed, "status": "failed"})
    assert not validation_matches_approval(profile, approval, {
        **completed, "result_json": {**result, "source_sha": "b" * 40}})


def test_validation_disabled_never_fetches_private_source(monkeypatch):
    job, profile, _ = _validation()
    queue = Queue()
    monkeypatch.setattr(project_worker, "get_settings", lambda: SimpleNamespace(
        e2b_profile_validation_enabled=False))

    def forbidden(*args, **kwargs):
        raise AssertionError("Disabled validation touched private source or E2B")

    assert project_worker.run_validation_job(
        job, queue, SimpleNamespace(get=forbidden),
        execute=forbidden, fetch_source=forbidden)
    assert queue.final == ("failed", None, "validation_disabled")


def test_validation_draft_change_rejects_before_source_fetch(monkeypatch):
    job, profile, _ = _validation()
    profile["status"] = "approved"
    queue = Queue()
    monkeypatch.setattr(project_worker, "get_settings", lambda: SimpleNamespace(
        e2b_profile_validation_enabled=True))

    def forbidden(*args, **kwargs):
        raise AssertionError("Changed profile touched private source or E2B")

    assert project_worker.run_validation_job(
        job, queue, SimpleNamespace(get=lambda owner, profile_id: profile),
        execute=forbidden, fetch_source=forbidden)
    assert queue.final == ("failed", None, "profile_changed")
