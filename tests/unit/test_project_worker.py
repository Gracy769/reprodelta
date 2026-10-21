"""The project worker does not manufacture findings on preflight failure."""

import hashlib
import uuid
from types import SimpleNamespace

from app.domain.contract import FIXTURE_CONTRACT, OutputSelector
from app.domain.project_run import ProjectRunSpec
from app.orchestrator import project_worker
from app.providers.e2b_runner import EnvironmentResult


def _job():
    spec = ProjectRunSpec(
        profile_id=uuid.uuid4(),
        provider="github",
        repository="owner/repo",
        change_number=7,
        base_sha="a" * 40,
        head_sha="b" * 40,
        source_sha256="c" * 64,
        profile_digest="d" * 64,
        contract_hash="e" * 64,
        old_environment_lock_sha256="f" * 64,
        new_environment_lock_sha256="0" * 64,
        sandbox_usd_per_second=0.0001,
        max_cost_usd=0.2,
    )
    return {"id": str(uuid.uuid4()), "lease_token": str(uuid.uuid4()),
            "owner_id": "github:123", "spec_json": spec.model_dump(mode="json")}


class Queue:
    def __init__(self):
        self.final = None
        self.cancelled = False

    def cancellation_requested(self, *_):
        return self.cancelled

    def finish(self, _job_id, _lease, *, status, result=None, error_code=None):
        self.final = (status, result, error_code)
        return True


def test_disabled_worker_never_fetches_source_or_executes(monkeypatch):
    queue = Queue()
    monkeypatch.setattr(
        project_worker, "get_settings", lambda: SimpleNamespace(e2b_project_runs_enabled=False)
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Source or sandbox was accessed with execution disabled")

    assert project_worker.run_claimed_job(
        _job(), queue, None, execute=forbidden, fetch_source=forbidden
    )
    assert queue.final == ("failed", None, "execution_disabled")


def test_approved_sample_records_observations_not_a_general_verdict(monkeypatch):
    job = _job()
    queue = Queue()
    spec = ProjectRunSpec.model_validate(job["spec_json"])
    contract = FIXTURE_CONTRACT.model_copy(
        update={"output_selector": OutputSelector(field_name="ratio")}
    )
    draft = SimpleNamespace(
        adapter_module="package.adapter", adapter_function="run",
        sample_input={"signal": [1.0] * 8}, contract=contract,
    )
    approval = SimpleNamespace(
        old=SimpleNamespace(template_id="old", numpy_version="1.26.4"),
        new=SimpleNamespace(template_id="new", numpy_version="2.0.2"),
    )
    monkeypatch.setattr(
        project_worker, "get_settings", lambda: SimpleNamespace(e2b_project_runs_enabled=True)
    )
    monkeypatch.setattr(
        project_worker, "_approved_inputs", lambda *_: ({}, draft, approval)
    )
    monkeypatch.setattr(
        project_worker, "check_eligibility", lambda *_: {"eligible": True}
    )

    async def source(*_):
        return {}, {"package/adapter.py": b"trusted test bytes"}

    versions = {"1.26.4": 0.4, "2.0.2": 0.6}

    def execute(**kwargs):
        assert kwargs["should_abort"]() is False
        assert 1 <= kwargs["timeout_seconds"] <= 900
        version = kwargs["expected_numpy"]
        return EnvironmentResult(
            version=version, sandbox_id=version.replace(".", ""), duration_seconds=1,
            source_sha256=spec.source_sha256,
            input_sha256=hashlib.sha256(b"sample").hexdigest(),
            output={"result": {"ratio": versions[version]}}, stderr="",
        )

    assert project_worker.run_claimed_job(
        job, queue, None, execute=execute, fetch_source=source
    )
    status, result, error = queue.final
    assert (status, error) == ("completed", None)
    assert result["changed_conclusion"] is True
    assert result["scope"] == "one approved sample input at one dependency-only PR revision"
    assert result["sandbox_seconds"] == 2


def test_owner_cancellation_prevents_any_sandbox(monkeypatch):
    queue = Queue()
    queue.cancelled = True
    monkeypatch.setattr(
        project_worker, "get_settings", lambda: SimpleNamespace(e2b_project_runs_enabled=True)
    )

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Cancelled job accessed the source or sandbox")

    assert project_worker.run_claimed_job(
        _job(), queue, None, execute=forbidden, fetch_source=forbidden
    )
    assert queue.final == ("cancelled", None, None)
